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
    ReviewMode,
    ReviewReport,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentCommitIndeterminateError
from app.services.review_revision_handoff import ReviewRevisionPlan
from app.workflows.chapter_production import (
    ChapterProductionStatus,
    ChapterReviewStage,
)
from app.workspace.hashing import sha256_content

_CONTRACT_VERSION = "chapter-production-v2"
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

def _normalized_plan(plan: ReviewRevisionPlan) -> ReviewRevisionPlan:
    return replace(
        plan,
        source_document_id=_normalize_uuid(plan.source_document_id),
        source_version_id=_normalize_uuid(plan.source_version_id),
        report_ids=tuple(_normalize_uuid(item) for item in plan.report_ids),
        target_segment_ids=tuple(_normalize_uuid(item) for item in plan.target_segment_ids),
    )

def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
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
    attempt: object, *, operation_key: str, attempt_id: str,
    checkpoint_index: int, report_input_hash: str,
) -> bool:
    return (
        type(attempt) is dict
        and attempt.get("key") == operation_key
        and attempt.get("attempt_id") == attempt_id
        and attempt.get("kind") == "review"
        and attempt.get("checkpoint_index") == checkpoint_index
        and attempt.get("report_input_hash") == report_input_hash
        and attempt.get("status") == _ATTEMPT_STATUS_CLAIMED
    )

@dataclass(frozen=True, slots=True, repr=False)
class ReviewRevisionIdentity:
    """Frozen, content-safe identity of one persisted corrective-revision candidate."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    document_id: UUID
    version_id: UUID
    source_version_id: UUID
    source_content_hash: str
    content_hash: str
    operation_key: str
    attempt_id: str
    report_ids: tuple[UUID, ...]
    report_input_hash: str

    def __post_init__(self) -> None:
        uuids = (
            self.project_id, self.chapter_id, self.workflow_run_id,
            self.document_id, self.version_id, self.source_version_id,
        )
        if (
            not all(_valid_uuid(v) for v in uuids)
            or not _valid_hash(self.source_content_hash)
            or not _valid_hash(self.content_hash)
            or not _valid_hash(self.operation_key)
            or not _valid_attempt_token(self.attempt_id)
            or type(self.report_ids) is not tuple or not self.report_ids
            or any(not _valid_uuid(v) for v in self.report_ids)
            or not _valid_hash(self.report_input_hash)
        ):
            raise _invalid() from None

    def __repr__(self) -> str:
        return "ReviewRevisionIdentity()"

def _replacements(plan: ReviewRevisionPlan) -> dict[UUID, str]:
    return {item.segment_id: item.content for item in plan.candidate.segments}

def _identity(
    version: DocumentVersion, plan: ReviewRevisionPlan,
    project_id: UUID, chapter_id: UUID, workflow_run_id: UUID,
) -> ReviewRevisionIdentity:
    return ReviewRevisionIdentity(
        project_id=project_id, chapter_id=chapter_id, workflow_run_id=workflow_run_id,
        document_id=plan.source_document_id, version_id=UUID(str(version.id)),
        source_version_id=plan.source_version_id, source_content_hash=plan.source_content_hash,
        content_hash=version.content_hash, operation_key=plan.operation_key,
        attempt_id=plan.attempt_id, report_ids=plan.report_ids,
        report_input_hash=plan.report_input_hash,
    )

def _validate_candidate(
    document: Document,
    version: DocumentVersion,
    content: str,
    plan: ReviewRevisionPlan,
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
        document.id != version.document_id or document.id != plan.source_document_id
        or document.project_id != project_id or document.chapter_id != chapter_id
        or document.type != DocumentType.CHAPTER_DRAFT.value
        or document.current_version_id != version.id
        or type(version.metadata_) is not dict or version.metadata_ != expected_metadata
        or version.parent_version_id != plan.source_version_id
        or version.workflow_run_id != workflow_run_id
        or version.source != DocumentSource.WRITER_AGENT.value
        or version.actor_user_id is not None or version.agent_role != "revision_agent"
        or version.content_hash != sha256_content(content)
    ):
        raise _reconcile() from None

async def _candidates(
    session: object, plan: ReviewRevisionPlan,
    project_id: UUID, chapter_id: UUID, workflow_run_id: UUID,
) -> list[tuple[Document, DocumentVersion]]:
    metadata_key = {"contract_version": _CONTRACT_VERSION, "operation_key": plan.operation_key}
    metadata_attempt = {"attempt_id": plan.attempt_id}
    versions = list(
        await session.scalars(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.parent_version_id == plan.source_version_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
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
                Document.project_id == project_id, Document.chapter_id == chapter_id,
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
    """Release the exact claimed review attempt only when the plan fields are usable."""

    key = getattr(plan, "operation_key", None)
    token = getattr(plan, "attempt_id", None)
    index = getattr(plan, "attempt_checkpoint_index", None)
    if (
        type(key) is not str or type(token) is not str
        or type(index) is not int or not _valid_uuid(workflow_run_id)
    ):
        return
    await service._release_attempt(
        workflow_run_id, expected_key=key, expected_attempt_id=token,
        expected_kind="review", expected_checkpoint_index=index,
    )

def _validate_persist_inputs(
    plan: object, project_id: UUID, chapter_id: UUID,
    workflow_run_id: UUID, actor_user_id: UUID,
) -> None:
    if (
        type(plan) is not ReviewRevisionPlan
        or not all(_valid_uuid(value) for value in (project_id, chapter_id, workflow_run_id, actor_user_id))
        or not _valid_uuid(plan.source_document_id)
        or not _valid_uuid(plan.source_version_id)
        or not _valid_hash(plan.source_content_hash)
        or not _valid_hash(plan.operation_key)
        or not _valid_attempt_token(plan.attempt_id)
        or type(plan.attempt_checkpoint_index) is not int
        or plan.attempt_checkpoint_index < 0
        or type(plan.report_ids) is not tuple
        or not 1 <= len(plan.report_ids) <= 16
        or any(not _valid_uuid(value) for value in plan.report_ids)
        or not _valid_hash(plan.report_input_hash)
        or type(plan.target_segment_ids) is not tuple
        or not 1 <= len(plan.target_segment_ids) <= 64
        or any(not _valid_uuid(value) for value in plan.target_segment_ids)
    ):
        raise _invalid() from None

def _validate_finalize_inputs(identity: object, actor_user_id: object) -> None:
    valid = type(identity) is ReviewRevisionIdentity
    try:
        if valid:
            identity.__post_init__()  # type: ignore[attr-defined]
    except BaseException:
        valid = False
    if not valid or not _valid_uuid(actor_user_id):
        raise _invalid() from None

async def _revalidate_revision_prewrite(
    service: object, *, plan: ReviewRevisionPlan, project_id: UUID,
    chapter_id: UUID, workflow_run_id: UUID, actor_user_id: UUID,
) -> None:
    current = await service._review_revision_context(
        project_id=project_id, chapter_id=chapter_id,
        workflow_run_id=workflow_run_id, report_ids=plan.report_ids,
        actor_user_id=actor_user_id,
    )
    if (
        current.document.id != plan.source_document_id
        or current.version.id != plan.source_version_id
        or current.version.content_hash != plan.source_content_hash
        or current.checkpoint.checkpoint_index != plan.attempt_checkpoint_index
    ):
        raise _invalid() from None
    if (
        current.segment_map.canonical_bytes() != plan.segment_map.canonical_bytes()
        or service._review_report_input_hash(current.reports) != plan.report_input_hash
    ):
        raise _invalid() from None
    attempt = service._run_metadata(current.run)["provider_attempt"]
    if not _exact_attempt(
        attempt, operation_key=plan.operation_key, attempt_id=plan.attempt_id,
        checkpoint_index=plan.attempt_checkpoint_index,
        report_input_hash=plan.report_input_hash,
    ):
        raise _reconcile() from None

def _review_stage(mode: str) -> ChapterReviewStage:
    return {
        ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
        ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
        ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
    }[mode]

def _report_slots(state: object) -> tuple[tuple[UUID, str, str], ...]:
    slots = (
        (
            UUID(state.editor_report_id) if state.editor_report_id is not None else None,
            ReviewMode.CHAPTER_EDITOR.value, "editor_agent",
        ),
        (
            UUID(state.chief_editor_report_id) if state.chief_editor_report_id is not None
            else None, ReviewMode.CHAPTER_CHIEF_FINAL.value, "chief_editor_agent",
        ),
        (
            UUID(state.lore_report_id) if state.lore_report_id is not None else None,
            ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent",
        ),
    )
    return tuple((rid, mode, role) for rid, mode, role in slots if rid)

async def _pending_action_count(service: object, run: object) -> int:
    return int(
        await service.session.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.PENDING.value,
            )
        )
    )

async def _source_and_reports(
    service: object, *, identity: ReviewRevisionIdentity,
    run: object, report_slots: tuple[tuple[UUID, str, str], ...],
) -> tuple[Document, DocumentVersion, list[ReviewReport]]:
    source_document = await service.session.scalar(
        select(Document).execution_options(populate_existing=True).where(
            Document.id == identity.document_id,
            Document.project_id == identity.project_id,
            Document.chapter_id == identity.chapter_id,
            Document.type == DocumentType.CHAPTER_DRAFT.value,
        ).with_for_update()
    )
    source_version = await service.session.scalar(
        select(DocumentVersion).execution_options(populate_existing=True).where(
            DocumentVersion.id == identity.source_version_id,
            DocumentVersion.document_id == identity.document_id,
        ).with_for_update()
    )
    if source_document is None or source_version is None:
        raise _reconcile() from None
    reports: list[ReviewReport] = []
    for report_id, expected_mode, expected_role in report_slots:
        report = await service.session.scalar(
            select(ReviewReport).execution_options(populate_existing=True).where(
                ReviewReport.id == report_id,
                ReviewReport.project_id == identity.project_id,
                ReviewReport.chapter_id == identity.chapter_id,
                ReviewReport.workflow_run_id == run.id,
                ReviewReport.target_document_id == identity.document_id,
                ReviewReport.target_version_id == identity.source_version_id,
                ReviewReport.review_mode == expected_mode,
                ReviewReport.reviewer_agent_role == expected_role,
            ).with_for_update()
        )
        if report is None:
            raise _reconcile() from None
        await service._validated_persisted_review_report(
            row=report, run=run, document=source_document,
            version=source_version, stage=_review_stage(expected_mode),
        )
        reports.append(report)
    return source_document, source_version, reports

async def _finalize_review_revision(
    service: object, identity: ReviewRevisionIdentity, actor_user_id: UUID
) -> ChapterProductionV2Updated:
    await service._require_project_owner(identity.project_id, actor_user_id)
    chapter = await service._chapter(identity.project_id, identity.chapter_id, lock=True)
    run = await service._run(identity.project_id, identity.chapter_id, identity.workflow_run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    report_slots = _report_slots(state)
    expected_reports = tuple(item[0] for item in report_slots)
    attempt = service._run_metadata(run)["provider_attempt"]
    pending_count = await _pending_action_count(service, run)
    if (
        state.status is not ChapterProductionStatus.REVIEW_REVISION
        or state.awaiting_user
        or state.action_request_id is not None
        or state.document_id != str(identity.document_id)
        or state.document_version_id != str(identity.source_version_id)
        or state.content_hash != identity.source_content_hash
        or identity.report_ids != expected_reports
        or pending_count != 0
    ):
        raise _invalid() from None
    if not _exact_attempt(
        attempt, operation_key=identity.operation_key, attempt_id=identity.attempt_id,
        checkpoint_index=checkpoint.checkpoint_index,
        report_input_hash=identity.report_input_hash,
    ):
        raise _reconcile() from None
    source_document, source_version, reports = await _source_and_reports(
        service, identity=identity, run=run, report_slots=report_slots,
    )
    trigger_mode = report_slots[-1][1]
    await service._validated_resolved_review_action(
        run=run,
        document=source_document,
        version=source_version,
        report=reports[-1],
        stage=_review_stage(trigger_mode),
    )
    if service._review_report_input_hash(reports) != identity.report_input_hash:
        raise _reconcile() from None
    document, version = await service._locked_current_revision(
        project_id=identity.project_id, chapter_id=identity.chapter_id,
        workflow_run_id=run.id, document_id=identity.document_id,
        version_id=identity.version_id, parent_version_id=identity.source_version_id,
        source=DocumentSource.WRITER_AGENT, actor_user_id=None,
        agent_role="revision_agent", operation_key=identity.operation_key,
        expected_attempt_id=identity.attempt_id,
    )
    if chapter.current_draft_document_id != document.id:
        raise _invalid() from None
    next_state = state.submit_review_revision(
        document_id=str(document.id),
        document_version_id=str(version.id),
        content_hash=version.content_hash,
    )
    service._set_attempt(run, None)
    service._append_state(run, checkpoint, next_state)
    await service._commit()
    return ChapterProductionV2Updated(
        workflow_run_id=run.id,
        draft_document_id=document.id,
        draft_version_id=version.id,
        action_request_id=None,
    )

class ReviewRevisionSaga:
    """Persist one corrective-revision candidate and reopen the next review gate."""

    def __init__(self, service: object, merge: object, prospective_map: object) -> None:
        self.service = service
        self._merge = merge
        self._prospective_map = prospective_map

    async def persist(
        self,
        plan: ReviewRevisionPlan,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ReviewRevisionIdentity:
        service = self.service
        wrote = False
        try:
            _validate_persist_inputs(
                plan, project_id, chapter_id, workflow_run_id, actor_user_id
            )
            plan = _normalized_plan(plan)
            await _revalidate_revision_prewrite(
                service, plan=plan, project_id=project_id, chapter_id=chapter_id,
                workflow_run_id=workflow_run_id, actor_user_id=actor_user_id,
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
                    document, version, revised_content, plan,
                    project_id, chapter_id, workflow_run_id,
                )
                identity = _identity(version, plan, project_id, chapter_id, workflow_run_id)
                await service._commit()
                return identity
            version = await service.documents.write_document(
                document_id=plan.source_document_id,
                content=revised_content,
                source=DocumentSource.WRITER_AGENT,
                expected_current_version_id=plan.source_version_id,
                agent_role="revision_agent",
                workflow_run_id=workflow_run_id,
                change_summary="Applied a Chapter Production V2 review revision.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": plan.operation_key,
                    "attempt_id": plan.attempt_id,
                },
            )
            wrote = True
            return _identity(version, plan, project_id, chapter_id, workflow_run_id)
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
        identity: ReviewRevisionIdentity,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        _validate_finalize_inputs(identity, actor_user_id)
        try:
            return await _finalize_review_revision(self.service, identity, actor_user_id)
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
    "ReviewRevisionIdentity",
    "ReviewRevisionSaga",
]
