"""Coordinate manual-edit resolution for Chapter Production V2.

The saga owns the frozen #114/#115 manual-edit transition: an authorized user
edit is persisted as a new immutable USER child through DocumentService, then a
fresh finalize session replays the exact durable evidence (single child
cardinality, resolved action envelope, transition binding) into the resolved
state and a new checkpoint.  It also owns the expiry contract: one PostgreSQL
clock_timestamp() value, taken after every required lock is held, authorizes
the edit only while database_now < expires_at.  It makes zero provider calls
and owns no persistence rules beyond the manual path; every write reuses the
facade's locked helpers through the service.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    ChapterSegmentMap,
    ChapterSegmentError,
    derive_chapter_segment_map,
    normalize_chapter_content,
)
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.author_accept_coordination import _StaleActionAdopted
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
    _valid_sha256,
)
from app.services.document_service import DocumentCommitIndeterminateError
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterProductionState,
)


_CONTRACT_VERSION = "chapter-production-v2"
_AUTHOR_ACTION_TYPE = "chapter_author_revision"


@dataclass(frozen=True, slots=True)
class _FinalizeEvidence:
    run: WorkflowRun
    chapter: Chapter
    metadata: dict[str, str]
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    action: ActionRequest
    document: Document
    version: DocumentVersion
    manual_key: str


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _expiry_precludes_resolution(expires_at: object, database_now: object) -> bool:
    """Fail closed once the single database clock reaches the action expiry."""
    return expires_at is not None and database_now >= expires_at


def _validated_prospective_map(
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content: str,
) -> ChapterSegmentMap:
    """Derive the complete #113 map before granting any canonical write."""

    try:
        project_id, chapter_id, document_id, version_id = (
            UUID(str(value)) for value in (project_id, chapter_id, document_id, version_id)
        )
        segment_map = derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            content=content,
            segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
        )
        segment_map.canonical_bytes()
        return segment_map
    except (ChapterSegmentError, UnicodeError, ValueError, TypeError, AttributeError):
        raise _invalid() from None


class ManualEditCoordinator:
    """Persist an authorized user edit and replay it into the resolved state."""

    def __init__(self, service: object) -> None:
        self.service = service

    async def submit(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
        content: str,
    ) -> ChapterProductionV2Updated:
        service = self.service
        try:
            context = await service._author_context(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
            )
        except _StaleActionAdopted as adopted:
            # The gate was already replaced by the author's own committed direct
            # USER edit (old action cancelled, child adopted) rather than a
            # manual-edit resolution of this gate, so the expiry check below is
            # intentionally skipped for this committed adoption path.
            return adopted.result
        database_now = await service.session.scalar(select(func.clock_timestamp()))
        if _expiry_precludes_resolution(context.action.expires_at, database_now):
            raise ChapterProductionV2ValidationError() from None
        operation_key, version = await self._prepare_and_persist(
            context=context,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            actor_user_id=actor_user_id,
            content=content,
        )
        return await self._finalize_manual_edit(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            actor_user_id=actor_user_id,
            document_id=context.document.id,
            version_id=version.id,
            old_binding=context.binding,
            expected_parent_version_id=context.version.id,
            operation_key=operation_key,
            finalize_actor_user_id=actor_user_id,
        )

    async def _prepare_and_persist(
        self,
        *,
        context: object,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
        content: str,
    ) -> tuple[str, object]:
        service = self.service
        try:
            normalized = normalize_chapter_content(content)
            if normalized != content or not normalized.strip():
                raise _invalid()
            operation_key = service._decision_operation_key(
                workflow_run_id, action_request_id, context.version.id, "manual"
            )
            service._resolve_action_row(
                context.action,
                status=ActionRequestStatus.REVISED,
                decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
                actor_user_id=actor_user_id,
            )
            _validated_prospective_map(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=context.document.id,
                version_id=context.version.id,
                content=normalized,
            )
            version = await service.documents.write_document(
                document_id=context.document.id,
                content=normalized,
                source=DocumentSource.USER,
                expected_current_version_id=context.version.id,
                actor_user_id=actor_user_id,
                workflow_run_id=workflow_run_id,
                change_summary="Applied an authorized Chapter Production V2 manual edit.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": operation_key,
                },
            )
        except DocumentCommitIndeterminateError:
            await service._rollback()
            raise ChapterProductionV2CommitIndeterminateError() from None
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await service._rollback()
            raise
        except Exception:
            await service._rollback()
            raise _invalid() from None
        return operation_key, version

    async def _finalize_manual_edit(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        version_id: UUID,
        old_binding: ChapterActionBinding,
        expected_parent_version_id: UUID,
        operation_key: str,
        finalize_actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        service = self.service
        try:
            evidence = await self._locked_finalize_evidence(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                action_request_id=action_request_id,
                actor_user_id=actor_user_id,
                document_id=document_id,
                version_id=version_id,
                expected_parent_version_id=expected_parent_version_id,
                operation_key=operation_key,
                finalize_actor_user_id=finalize_actor_user_id,
            )
            self._validate_finalize_evidence(
                evidence=evidence,
                project_id=project_id,
                chapter_id=chapter_id,
                old_binding=old_binding,
                operation_key=operation_key,
            )
            next_state = evidence.state.resolve_action(
                action=old_binding,
                decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
                document_id=str(evidence.document.id),
                document_version_id=str(evidence.version.id),
                content_hash=evidence.version.content_hash,
            )
            service._append_state(evidence.run, evidence.checkpoint, next_state)
            await service._commit()
            return ChapterProductionV2Updated(
                workflow_run_id=evidence.run.id,
                draft_document_id=evidence.document.id,
                draft_version_id=evidence.version.id,
                action_request_id=None,
            )
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ValidationError:
            await service._rollback()
            raise
        except Exception:
            await service._rollback()
            raise _invalid() from None

    async def _locked_finalize_evidence(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
        document_id: UUID,
        version_id: UUID,
        expected_parent_version_id: UUID,
        operation_key: str,
        finalize_actor_user_id: UUID,
    ) -> _FinalizeEvidence:
        service = self.service
        await service._require_project_owner(project_id, finalize_actor_user_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        await service._require_project_owner(project_id, actor_user_id)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        metadata = service._run_metadata(run)
        state, checkpoint = await service._locked_state(run)
        manual_key = service._decision_operation_key(
            run.id, action_request_id, expected_parent_version_id, "manual"
        )
        action = await service.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_request_id,
                ActionRequest.workflow_run_id == run.id,
                ActionRequest.status == ActionRequestStatus.REVISED.value,
                ActionRequest.user_decision == ChapterActionDecision.SUBMIT_MANUAL_EDIT.value,
                ActionRequest.resolved_by_id == actor_user_id,
                ~select(ActionRequest.id)
                .where(
                    ActionRequest.workflow_run_id == run.id,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
                .exists(),
                select(func.count(DocumentVersion.id))
                .where(DocumentVersion.parent_version_id == expected_parent_version_id)
                .scalar_subquery()
                == 1,
            )
            .with_for_update()
        )
        document, version = await service._locked_current_revision(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=run.id,
            document_id=document_id,
            version_id=version_id,
            parent_version_id=expected_parent_version_id,
            source=DocumentSource.USER,
            actor_user_id=actor_user_id,
            agent_role=None,
            operation_key=operation_key,
        )
        return _FinalizeEvidence(
            run=run,
            chapter=chapter,
            metadata=metadata,
            state=state,
            checkpoint=checkpoint,
            action=action,
            document=document,
            version=version,
            manual_key=manual_key,
        )

    @staticmethod
    def _validate_finalize_evidence(
        *,
        evidence: _FinalizeEvidence,
        project_id: UUID,
        chapter_id: UUID,
        old_binding: ChapterActionBinding,
        operation_key: str,
    ) -> None:
        action = evidence.action
        action_key = (
            action.metadata_.get("operation_key")
            if action is not None and type(action.metadata_) is dict
            else None
        )
        expected_action = {
            "contract_version": _CONTRACT_VERSION,
            "action_kind": ChapterActionKind.AUTHOR_REVISION.value,
            "document_id": old_binding.document_id,
            "document_version_id": old_binding.document_version_id,
            "content_hash": old_binding.content_hash,
            "operation_key": action_key,
        }
        if (
            action is None
            or operation_key != evidence.manual_key
            or evidence.chapter.current_draft_document_id != evidence.document.id
            or any(
                evidence.metadata[key] is not None
                for key in ("provider_attempt", "reviewer_claim")
            )
            or action.project_id != project_id
            or action.chapter_id != chapter_id
            or action.request_type != _AUTHOR_ACTION_TYPE
            or action.prompt != "Review the current chapter draft."
            or action.options != ["accept", "request_revision", "submit_manual_edit"]
            or action.default_option != "accept"
            or action.user_feedback is not None
            or action.resolved_at is None
            or action.expires_at is not None
            or not _valid_sha256(action_key)
            or action.metadata_ != expected_action
        ):
            raise _invalid()


__all__ = ["ManualEditCoordinator"]
