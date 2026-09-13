"""Execute an immutable Studio submission through the existing feedback saga."""

import json
from dataclasses import replace
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.agents.chapter_writer_contracts import _StrictChapterModel, _canonical_uuid
from app.core.errors import ConflictError
from app.models import ActionRequest, ActionRequestStatus, Document, DocumentVersion, StudioFeedbackSubmission
from app.services.author_accept_coordination import _expiry_precludes_resolution
from app.services.chapter_production_recovery_evidence import _validate_saved_user_chain
from app.services.chapter_production_v2_contracts import ChapterProductionV2Updated
from app.services.studio_feedback import FeedbackInvalidError, utf16_slice
from app.workflows.chapter_production import ChapterActionKind, ChapterProductionStatus
from app.workspace.hashing import sha256_content


class StudioFeedbackRevisionRequest(_StrictChapterModel):
    submission_id: UUID
    document_id: UUID
    version_id: UUID
    action_request_id: UUID

    @field_validator("submission_id", "action_request_id", mode="before")
    @classmethod
    def valid_id(cls, value):
        return _canonical_uuid(value)


class StudioFeedbackRevisionIntent(_StrictChapterModel):
    request: StudioFeedbackRevisionRequest
    actor_user_id: UUID
    feedback_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    result_version_id: UUID | None = None

    @field_validator("actor_user_id", "result_version_id", mode="before")
    @classmethod
    def valid_id(cls, value):
        return _canonical_uuid(value) if value is not None else None


def feedback_revision_intent(run):
    value = getattr(run, "metadata_", {}).get("studio_feedback_revision")
    return StudioFeedbackRevisionIntent.model_validate_json(json.dumps(value)) if value else None


async def _submission_input(service, project_id, chapter_id, request):
    submission = await service.session.get(StudioFeedbackSubmission, request.submission_id)
    if (submission is None or submission.chapter_id != chapter_id or submission.region != "draft"
            or submission.document_id != request.document_id or submission.source_version_id != request.version_id):
        raise ConflictError("The feedback submission does not match this draft.")
    content = await service.documents.read_version_content(request.document_id, request.version_id)
    segments = await service.documents.derive_chapter_segment_map(project_id=project_id,
        chapter_id=chapter_id, document_id=request.document_id, version_id=request.version_id)
    selected = set()
    comments = []
    for comment in submission.comments:
        if not comment["text"].strip():
            continue
        if (comment.get("orphaned") or comment["start"] >= comment["end"]
                or utf16_slice(content, comment["start"], comment["end"]) != comment["quote"]):
            raise FeedbackInvalidError("A selected comment has lost its source position. Remove it before requesting revision.")
        start = len(utf16_slice(content, 0, comment["start"]).encode("utf-8"))
        end = len(utf16_slice(content, 0, comment["end"]).encode("utf-8"))
        targets = [s.segment_id for s in segments.segments if s.start_byte < end and s.end_byte > start]
        if not targets:
            raise FeedbackInvalidError("A selected comment has no source segment.")
        selected.update(targets)
        comments.append({"comment_id": comment["id"], "quote": comment["quote"],
                         "instruction": comment["text"], "segment_ids": [str(s) for s in targets]})
    if submission.requirements.strip():
        selected.update(s.segment_id for s in segments.segments)
    if not selected:
        raise FeedbackInvalidError("There is no revision instruction in this submission.")
    feedback = json.dumps({"submission_id": str(submission.id), "requirements": submission.requirements,
                          "comments": comments}, ensure_ascii=False, separators=(",", ":"))
    # The existing feedback provider contract is bounded; never silently truncate instructions.
    if len(feedback) > 8000:
        raise FeedbackInvalidError("The submitted feedback exceeds the revision input budget. Submit fewer comments.")
    service._validated_feedback(feedback)
    targets = tuple(s.segment_id for s in segments.segments if s.segment_id in selected)
    return feedback, targets


async def request_studio_feedback_revision(service, project_id, chapter_id, run_id, actor_id, request):
    try:
        await service._require_project_owner(project_id, actor_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, run_id, lock=True)
        state, checkpoint = await service._locked_state(run)
        feedback, targets = await _submission_input(service, project_id, chapter_id, request)
        previous = feedback_revision_intent(run)
        if previous and previous.request.submission_id == request.submission_id:
            if (previous.request != request or previous.actor_user_id != actor_id
                    or previous.feedback_hash != sha256_content(feedback) or previous.target_segment_ids != targets):
                raise ConflictError("The original feedback revision inputs changed.")
            if previous.result_version_id:
                await service._commit()
                return ChapterProductionV2Updated(workflow_run_id=run_id, draft_document_id=request.document_id,
                    draft_version_id=previous.result_version_id,
                    action_request_id=UUID(state.action_request_id) if state.action_request_id else None)
            await service._commit()
            if state.status is ChapterProductionStatus.DRAFTING:
                recovered = await service.reconcile_indeterminate(project_id, chapter_id, run_id, actor_user_id=actor_id)
                if recovered.status is ChapterProductionStatus.DRAFTING:
                    raise ConflictError("The feedback revision is still running. Retry after it finishes.")
                # Reconciliation may finalize the already persisted candidate.
                return await request_studio_feedback_revision(service, project_id, chapter_id, run_id, actor_id, request)
        else:
            if previous and previous.result_version_id is None:
                raise ConflictError("Recover the earlier feedback revision first.")
            if (state.status is not ChapterProductionStatus.AUTHOR_REVISION or not state.awaiting_user
                    or state.action_kind is not ChapterActionKind.AUTHOR_REVISION
                    or state.action_request_id != str(request.action_request_id)
                    or state.document_id != str(request.document_id) or chapter.current_draft_document_id != request.document_id):
                raise ConflictError("The author gate has changed.")
            action = await service.session.get(ActionRequest, request.action_request_id, with_for_update=True)
            document = await service.session.get(Document, request.document_id, with_for_update=True)
            version = await service.session.get(DocumentVersion, request.version_id, with_for_update=True)
            metadata = service._action_metadata(action)
            if (document is None or document.current_version_id != request.version_id or version is None
                    or version.document_id != request.document_id or action.workflow_run_id != run_id
                    or action.status != ActionRequestStatus.PENDING.value
                    or metadata["document_version_id"] != state.document_version_id
                    or metadata["content_hash"] != state.content_hash):
                raise ConflictError("The saved draft or author action has changed.")
            database_now = await service.session.scalar(select(func.clock_timestamp()))
            if _expiry_precludes_resolution(action.expires_at, database_now):
                raise ConflictError("The author action has expired.")
            if state.document_version_id != str(request.version_id):
                await _validate_saved_user_chain(service, document.id, UUID(state.document_version_id), version, actor_id)
                # Keep the real author gate: requesting a revision does not approve the saved edits.
                state = replace(state, document_version_id=str(version.id), content_hash=version.content_hash)
                action.metadata_ = {**action.metadata_, "document_version_id": str(version.id), "content_hash": version.content_hash}
                service._append_state(run, checkpoint, state)
            intent = StudioFeedbackRevisionIntent(request=request, actor_user_id=actor_id,
                feedback_hash=sha256_content(feedback), target_segment_ids=targets)
            run.metadata_ = {**run.metadata_, "studio_feedback_revision": intent.model_dump(mode="json")}
            await service._commit()
        return await service.request_user_feedback_revision(project_id, chapter_id, run_id,
            request.action_request_id, actor_user_id=actor_id, feedback=feedback, target_segment_ids=targets)
    except Exception:
        await service._rollback()
        raise


def validate_feedback_revision_input(run, action_id, version_id, feedback, targets):
    intent = feedback_revision_intent(run)
    if intent and intent.result_version_id is None and (
        intent.request.action_request_id != action_id or intent.request.version_id != version_id
        or intent.feedback_hash != sha256_content(feedback) or intent.target_segment_ids != tuple(targets)
    ):
        raise ConflictError("The pending Studio feedback revision has different inputs.")


def record_feedback_revision_result(run, action_id, version_id):
    intent = feedback_revision_intent(run)
    if intent and intent.result_version_id is None:
        if intent.request.action_request_id != action_id:
            raise ConflictError("The feedback result belongs to another action.")
        run.metadata_ = {**run.metadata_, "studio_feedback_revision":
            intent.model_copy(update={"result_version_id": version_id}).model_dump(mode="json")}
