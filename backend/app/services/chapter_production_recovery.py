"""Scoped recovery facade for Chapter Production V2.

The heavy recovery and state-loading bodies live in
``chapter_production_recovery_impl``; this module exposes the small
``ChapterProductionRecovery`` coordinator the facade constructs.  Keeping the
coordinator here preserves the module/class/function size contract while the
facade keeps only thin delegates.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.models import (
    Chapter,
    Document,
    DocumentSource,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_production_recovery_impl import (
    _AuthorContext,
    _ChapterProductionRecoveryImpl,
    _ReviewRevisionContext,
    _review_report_slots,  # noqa: F401  (re-export)
    verified_snapshot_content,
)
from app.workflows.chapter_production import (
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewStage,
)


class ChapterProductionRecovery:
    """Thin coordinator over the recovery implementation module."""

    def __init__(self, service: object) -> None:
        self._impl = _ChapterProductionRecoveryImpl(service)

    async def load_state(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        return await self._impl.load_state(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )

    async def reconcile_review_route(
        self,
        service: object,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> ChapterProductionState:
        return await self._impl.reconcile_review_route(
            service,
            project_id,
            chapter_id,
            workflow_run_id,
            actor_user_id=actor_user_id,
        )

    async def fail_provider(
        self,
        service: object,
        workflow_run_id: UUID,
        failure_code: ChapterFailureCode,
        *,
        expected_status: ChapterProductionStatus,
        expected_checkpoint_index: int,
        expected_attempt_key: str,
        expected_attempt_id: str,
    ) -> bool:
        return await self._impl.fail_provider(
            service,
            workflow_run_id,
            failure_code,
            expected_status=expected_status,
            expected_checkpoint_index=expected_checkpoint_index,
            expected_attempt_key=expected_attempt_key,
            expected_attempt_id=expected_attempt_id,
        )

    async def recover_failed_attempt(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
        kind: str,
        action_request_id: UUID | None = None,
        target_segment_ids: Sequence[UUID] = (),
        feedback_hash: str | None = None,
        report_ids: Sequence[UUID] = (),
        restore_feedback: bool = False,
    ) -> None:
        return await self._impl.recover_failed_attempt(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            kind=kind,
            action_request_id=action_request_id,
            target_segment_ids=target_segment_ids,
            feedback_hash=feedback_hash,
            report_ids=report_ids,
            restore_feedback=restore_feedback,
        )

    async def release_attempt(
        self,
        service: object,
        workflow_run_id: UUID,
        *,
        expected_key: str,
        expected_attempt_id: str,
        expected_kind: str,
        expected_checkpoint_index: int,
        restore_feedback: bool = False,
    ) -> None:
        return await self._impl.release_attempt(
            service,
            workflow_run_id,
            expected_key=expected_key,
            expected_attempt_id=expected_attempt_id,
            expected_kind=expected_kind,
            expected_checkpoint_index=expected_checkpoint_index,
            restore_feedback=restore_feedback,
        )

    async def author_context(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
    ) -> _AuthorContext:
        return await self._impl.author_context(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            actor_user_id=actor_user_id,
        )

    async def review_revision_context(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        report_ids: Sequence[UUID],
        actor_user_id: UUID,
    ) -> _ReviewRevisionContext:
        return await self._impl.review_revision_context(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            report_ids=report_ids,
            actor_user_id=actor_user_id,
        )

    async def locked_review_document(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        state: ChapterProductionState,
        chapter: Chapter,
    ) -> tuple[Document, DocumentVersion]:
        return await self._impl.locked_review_document(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            state=state,
            chapter=chapter,
        )

    async def exact_review_report_count(
        self,
        service: object,
        *,
        run: WorkflowRun,
        version: DocumentVersion,
        stage: ChapterReviewStage,
    ) -> int:
        return await self._impl.exact_review_report_count(
            service,
            run=run,
            version=version,
            stage=stage,
        )

    async def locked_state(
        self, service: object, run: WorkflowRun
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]:
        return await self._impl.locked_state(service, run)

    @staticmethod
    def verified_snapshot_content(
        document: Document, version: DocumentVersion
    ) -> str:
        return verified_snapshot_content(document, version)

    async def locked_current_revision(
        self,
        service: object,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        document_id: UUID,
        version_id: UUID,
        parent_version_id: UUID,
        source: DocumentSource,
        actor_user_id: UUID | None,
        agent_role: str | None,
        operation_key: str,
        expected_attempt_id: str | None = None,
    ) -> tuple[Document, DocumentVersion]:
        return await self._impl.locked_current_revision(
            service,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            document_id=document_id,
            version_id=version_id,
            parent_version_id=parent_version_id,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            operation_key=operation_key,
            expected_attempt_id=expected_attempt_id,
        )


__all__ = [
    "ChapterProductionRecovery",
    "_AuthorContext",
    "_ReviewRevisionContext",
    "_review_report_slots",
]
