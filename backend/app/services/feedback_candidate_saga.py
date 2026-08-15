from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentCommitIndeterminateError
from app.services.feedback_revision_handoff import FeedbackRevisionPlan
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterProductionStatus,
)
from app.workspace.hashing import sha256_content

_CONTRACT_VERSION = "chapter-production-v2"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"
_ATTEMPT_STATUS_CLAIMED = "claimed"

def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()

def _reconcile() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()

def _valid_uuid(value: object) -> bool:
    if type(value) is UUID and value.int != 0:
        return True
    try:
        return value.int != 0 and str(value) == str(UUID(str(value)))  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return False

def _normalize_uuid(value: object) -> UUID:
    if not _valid_uuid(value):
        raise _invalid() from None
    return UUID(str(value))


def _normalized_plan(plan: FeedbackRevisionPlan) -> FeedbackRevisionPlan:
    return replace(plan, source_document_id=_normalize_uuid(plan.source_document_id),
                   source_version_id=_normalize_uuid(plan.source_version_id))


def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_attempt_token(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.int != 0 and str(parsed) == value


def _exact_attempt(
    attempt: object,
    *,
    operation_key: str,
    attempt_id: str,
    checkpoint_index: int,
) -> bool:
    """Return True only for the one exact claimed feedback attempt token."""

    return (
        type(attempt) is dict
        and attempt.get("key") == operation_key
        and attempt.get("attempt_id") == attempt_id
        and attempt.get("kind") == "feedback"
        and attempt.get("checkpoint_index") == checkpoint_index
        and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED
    )


@dataclass(frozen=True, slots=True, repr=False)
class FeedbackCandidateIdentity:
    """Frozen, content-safe identity of one persisted feedback candidate."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    action_request_id: UUID
    document_id: UUID
    version_id: UUID
    source_version_id: UUID
    source_content_hash: str
    content_hash: str
    operation_key: str
    attempt_id: str

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_uuid(value)
                for value in (
                    self.project_id,
                    self.chapter_id,
                    self.workflow_run_id,
                    self.action_request_id,
                    self.document_id,
                    self.version_id,
                    self.source_version_id,
                )
            )
            or not _valid_hash(self.source_content_hash)
            or not _valid_hash(self.content_hash)
            or not _valid_hash(self.operation_key)
            or not _valid_attempt_token(self.attempt_id)
        ):
            raise _invalid() from None

    def __repr__(self) -> str:
        return "FeedbackCandidateIdentity()"


def _replacements(plan: FeedbackRevisionPlan) -> dict[UUID, str]:
    return {item.segment_id: item.content for item in plan.candidate.segments}


def _identity(
    version: DocumentVersion,
    plan: FeedbackRevisionPlan,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_request_id: UUID,
) -> FeedbackCandidateIdentity:
    return FeedbackCandidateIdentity(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=workflow_run_id,
        action_request_id=action_request_id,
        document_id=plan.source_document_id,
        version_id=UUID(str(version.id)),
        source_version_id=plan.source_version_id,
        source_content_hash=plan.source_content_hash,
        content_hash=version.content_hash,
        operation_key=plan.operation_key,
        attempt_id=plan.attempt_id,
    )


def _validate_candidate(
    document: Document,
    version: DocumentVersion,
    content: str,
    plan: FeedbackRevisionPlan,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
) -> None:
    expected_metadata = {
        "contract_version": _CONTRACT_VERSION,
        "operation_key": plan.operation_key,
        "attempt_id": plan.attempt_id,
    }
    if (
        document.id != version.document_id
        or document.id != plan.source_document_id
        or document.project_id != project_id
        or document.chapter_id != chapter_id
        or document.type != DocumentType.CHAPTER_DRAFT.value
        or document.current_version_id != version.id
        or type(version.metadata_) is not dict
        or version.metadata_ != expected_metadata
        or version.parent_version_id != plan.source_version_id
        or version.workflow_run_id != workflow_run_id
        or version.source != DocumentSource.WRITER_AGENT.value
        or version.actor_user_id is not None
        or version.agent_role != "revision_agent"
        or version.content_hash != sha256_content(content)
    ):
        raise _reconcile() from None


async def _candidates(
    session: object,
    plan: FeedbackRevisionPlan,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
) -> list[tuple[Document, DocumentVersion]]:
    metadata_key = {
        "contract_version": _CONTRACT_VERSION,
        "operation_key": plan.operation_key,
    }
    metadata_attempt = {"attempt_id": plan.attempt_id}
    versions = list(
        await session.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.parent_version_id == plan.source_version_id,
                or_(
                    DocumentVersion.workflow_run_id == workflow_run_id,
                    DocumentVersion.metadata_.contains(metadata_key),
                    DocumentVersion.metadata_.contains(metadata_attempt),
                ),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if not versions:
        return []
    documents = list(
        await session.scalars(
            select(Document)
            .options(selectinload(Document.project))
            .where(
                Document.id.in_(tuple(version.document_id for version in versions)),
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    by_id = {document.id: document for document in documents}
    if len(documents) != len({version.document_id for version in versions}):
        raise _reconcile() from None
    return [(by_id[version.document_id], version) for version in versions]


async def _release(service: object, plan: object, workflow_run_id: UUID) -> None:
    """Release the exact claimed attempt only when the plan fields are usable."""

    key = getattr(plan, "operation_key", None)
    token = getattr(plan, "attempt_id", None)
    index = getattr(plan, "attempt_checkpoint_index", None)
    if (
        type(key) is not str
        or type(token) is not str
        or type(index) is not int
        or not _valid_uuid(workflow_run_id)
    ):
        return
    await service._release_attempt(
        workflow_run_id,
        expected_key=key,
        expected_attempt_id=token,
        expected_kind="feedback",
        expected_checkpoint_index=index,
        restore_feedback=True,
    )


def _validate_persist_inputs(
    plan: object,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_request_id: UUID,
    actor_user_id: UUID,
) -> None:
    if (
        type(plan) is not FeedbackRevisionPlan
        or not all(
            _valid_uuid(value)
            for value in (project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id)
        )
        or not _valid_uuid(plan.source_document_id)
        or not _valid_uuid(plan.source_version_id)
        or not _valid_hash(plan.source_content_hash)
        or not _valid_hash(plan.operation_key)
        or not _valid_attempt_token(plan.attempt_id)
        or type(plan.attempt_checkpoint_index) is not int
        or plan.attempt_checkpoint_index < 0
        or type(plan.feedback) is not str
        or type(plan.target_segment_ids) is not tuple
    ):
        raise _invalid() from None


def _validate_finalize_inputs(identity: object, actor_user_id: object) -> None:
    valid = type(identity) is FeedbackCandidateIdentity
    try:
        if valid:
            identity.__post_init__()  # type: ignore[attr-defined]
    except BaseException:
        valid = False
    if not valid or not _valid_uuid(actor_user_id):
        raise _invalid() from None


async def _revalidate_revision_prewrite(
    service: object,
    *,
    plan: FeedbackRevisionPlan,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    action_request_id: UUID,
    actor_user_id: UUID,
) -> None:
    await service._require_project_owner(project_id, actor_user_id)
    chapter = await service._chapter(project_id, chapter_id, lock=True)
    run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    attempt = service._run_metadata(run)["provider_attempt"]
    if not _exact_attempt(
        attempt,
        operation_key=plan.operation_key,
        attempt_id=plan.attempt_id,
        checkpoint_index=plan.attempt_checkpoint_index,
    ):
        raise ChapterProductionV2ReconciliationError() from None
    if (
        state.status is not ChapterProductionStatus.DRAFTING
        or state.awaiting_user
        or state.document_id != str(plan.source_document_id)
        or state.document_version_id != str(plan.source_version_id)
        or state.content_hash != plan.source_content_hash
        or checkpoint.checkpoint_index != plan.attempt_checkpoint_index
    ):
        raise _invalid() from None
    action = await service.session.scalar(
        select(ActionRequest)
        .where(
            ActionRequest.id == action_request_id,
            ActionRequest.workflow_run_id == run.id,
            ActionRequest.status == ActionRequestStatus.REVISED.value,
            ActionRequest.user_decision == ChapterActionDecision.REQUEST_REVISION.value,
        )
        .with_for_update()
    )
    if (
        action is None
        or action.user_feedback != plan.feedback
        or action.resolved_by_id is None
        or str(action.resolved_by_id) != str(actor_user_id)
        or type(action.metadata_) is not dict
        or action.metadata_.get("document_id") != str(plan.source_document_id)
        or action.metadata_.get("document_version_id") != str(plan.source_version_id)
    ):
        raise _invalid() from None
    document = await service.session.scalar(
        select(Document)
        .options(selectinload(Document.project), selectinload(Document.current_version))
        .where(
            Document.id == plan.source_document_id,
            Document.project_id == project_id,
            Document.chapter_id == chapter_id,
            Document.type == DocumentType.CHAPTER_DRAFT.value,
            Document.current_version_id == plan.source_version_id,
        )
        .with_for_update()
    )
    version = await service.session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == plan.source_version_id,
            DocumentVersion.document_id == plan.source_document_id,
            DocumentVersion.content_hash == plan.source_content_hash,
        )
        .with_for_update()
    )
    if document is None or version is None or chapter.current_draft_document_id != document.id:
        raise _invalid() from None
    await service.documents.derive_chapter_segment_map(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document.id,
        version_id=version.id,
    )


async def _finalize_feedback_revision(
    service: object, identity: FeedbackCandidateIdentity, actor_user_id: UUID
) -> ChapterProductionV2Updated:
    await service._require_project_owner(identity.project_id, actor_user_id)
    chapter = await service._chapter(identity.project_id, identity.chapter_id, lock=True)
    run = await service._run(identity.project_id, identity.chapter_id, identity.workflow_run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    if state.status is not ChapterProductionStatus.DRAFTING or state.awaiting_user:
        raise _invalid() from None
    if (
        state.document_id != str(identity.document_id)
        or state.document_version_id != str(identity.source_version_id)
        or state.content_hash != identity.source_content_hash
    ):
        raise _invalid() from None
    attempt = service._run_metadata(run)["provider_attempt"]
    if not _exact_attempt(
        attempt,
        operation_key=identity.operation_key,
        attempt_id=identity.attempt_id,
        checkpoint_index=checkpoint.checkpoint_index,
    ):
        raise ChapterProductionV2ReconciliationError() from None
    old_action = await service.session.scalar(
        select(ActionRequest)
        .where(
            ActionRequest.id == identity.action_request_id,
            ActionRequest.workflow_run_id == run.id,
            ActionRequest.status == ActionRequestStatus.REVISED.value,
        )
        .with_for_update()
    )
    document, version = await service._locked_current_revision(
        project_id=identity.project_id,
        chapter_id=identity.chapter_id,
        workflow_run_id=run.id,
        document_id=identity.document_id,
        version_id=identity.version_id,
        parent_version_id=identity.source_version_id,
        source=DocumentSource.WRITER_AGENT,
        actor_user_id=None,
        agent_role="revision_agent",
        operation_key=identity.operation_key,
        expected_attempt_id=identity.attempt_id,
    )
    if old_action is None or chapter.current_draft_document_id != document.id:
        raise _invalid() from None
    pending_count = await service.session.scalar(
        select(func.count())
        .select_from(ActionRequest)
        .where(
            ActionRequest.workflow_run_id == run.id,
            ActionRequest.status == ActionRequestStatus.PENDING.value,
        )
    )
    if pending_count != 0:
        raise _invalid() from None
    action = service._new_author_action(
        run=run, project_id=identity.project_id, chapter_id=identity.chapter_id,
        document=document, version=version, operation_key=identity.operation_key,
    )
    service.session.add(action)
    await service.session.flush()
    binding = service._binding_for_new_action(
        action=action, run=run, chapter_id=identity.chapter_id,
        document=document, version=version,
    )
    next_state = state.submit_draft(
        document_id=str(document.id), document_version_id=str(version.id),
        content_hash=version.content_hash, action=binding,
    )
    service._set_attempt(run, None)
    service._append_state(run, checkpoint, next_state)
    await service._commit()
    return ChapterProductionV2Updated(
        workflow_run_id=run.id, draft_document_id=document.id,
        draft_version_id=version.id, action_request_id=action.id,
    )


class FeedbackCandidateSaga:
    """Persist one feedback candidate and reopen the next author gate."""

    def __init__(self, service: object, merge: object, prospective_map: object) -> None:
        self.service = service
        self._merge = merge
        self._prospective_map = prospective_map

    async def persist(
        self,
        plan: FeedbackRevisionPlan,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
    ) -> FeedbackCandidateIdentity:
        service = self.service
        wrote = False
        try:
            _validate_persist_inputs(
                plan, project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id)
            plan = _normalized_plan(plan)
            await _revalidate_revision_prewrite(
                service, plan=plan, project_id=project_id, chapter_id=chapter_id,
                workflow_run_id=workflow_run_id, action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
            source_content = await service.documents.read_version_content(
                plan.source_document_id, plan.source_version_id
            )
            revised_content = self._merge(source_content, plan.segment_map, _replacements(plan))
            self._prospective_map(
                project_id=project_id, chapter_id=chapter_id,
                document_id=plan.source_document_id, version_id=plan.source_version_id,
                content=revised_content,
            )
            candidates = await _candidates(
                service.session, plan, project_id, chapter_id, workflow_run_id
            )
            if len(candidates) > 1:
                raise _reconcile() from None
            if candidates:
                document, version = candidates[0]
                _validate_candidate(
                    document, version, revised_content, plan, project_id, chapter_id, workflow_run_id,
                )
                identity = _identity(
                    version, plan, project_id, chapter_id, workflow_run_id, action_request_id
                )
                await service._commit()
                return identity
            version = await service.documents.write_document(
                document_id=plan.source_document_id,
                content=revised_content,
                source=DocumentSource.WRITER_AGENT,
                expected_current_version_id=plan.source_version_id,
                agent_role="revision_agent",
                workflow_run_id=workflow_run_id,
                change_summary="Applied a Chapter Production V2 feedback revision.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": plan.operation_key,
                    "attempt_id": plan.attempt_id,
                },
            )
            wrote = True
            return _identity(
                version, plan, project_id, chapter_id, workflow_run_id, action_request_id
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except DocumentCommitIndeterminateError:
            await service._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None
        except (ChapterProductionV2ValidationError, ChapterProductionV2ReconciliationError):
            if wrote:
                await service._rollback()
                raise ChapterProductionV2CommitIndeterminateError() from None
            await _release(service, plan, workflow_run_id)
            raise
        except Exception:
            if wrote:
                await service._rollback()
                raise ChapterProductionV2CommitIndeterminateError() from None
            await _release(service, plan, workflow_run_id)
            raise _invalid() from None

    async def finalize(
        self,
        identity: FeedbackCandidateIdentity,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        _validate_finalize_inputs(identity, actor_user_id)
        try:
            return await _finalize_feedback_revision(self.service, identity, actor_user_id)
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await self.service._rollback()
            raise
        except ChapterProductionV2ValidationError:
            await self.service._rollback()
            raise
        except Exception:
            await self.service._rollback()
            raise _invalid() from None


__all__ = [
    "FeedbackCandidateIdentity",
    "FeedbackCandidateSaga",
]
