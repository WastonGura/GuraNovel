"""Route draft-specific reconciliation to the extracted phase modules."""

from __future__ import annotations

from uuid import UUID

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
)
from app.workflows.chapter_production import ChapterProductionStatus


class ChapterDraftRevisionCoordinator:
    """Session-free routing layer for draft revision recovery."""

    def __init__(
        self, service: object, *, feedback_saga: object, review_saga: object,
        manual_edit: object,
    ) -> None:
        self._service = service
        self._feedback_saga = feedback_saga
        self._review_saga = review_saga
        self._manual_edit = manual_edit

    async def reconcile(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *,
        actor_user_id: UUID,
    ) -> object | None:
        service = self._service
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        await service._require_project_owner(project_id, actor_user_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await service._locked_state(run)
        if state.status is ChapterProductionStatus.DRAFTING:
            return await self._feedback_saga.reconcile_drafting(
                project_id=project_id, chapter_id=chapter_id, run=run, state=state,
                checkpoint=checkpoint, chapter=chapter, actor_user_id=actor_user_id)
        if state.status is ChapterProductionStatus.REVIEW_REVISION:
            return await self._review_saga.reconcile_review(
                project_id=project_id, chapter_id=chapter_id, run=run, state=state,
                checkpoint=checkpoint, chapter=chapter, actor_user_id=actor_user_id)
        if state.status is ChapterProductionStatus.AUTHOR_REVISION:
            return await self._manual_edit.reconcile_manual(
                project_id=project_id, chapter_id=chapter_id, run=run, state=state,
                chapter=chapter, actor_user_id=actor_user_id)
        return None

    async def acknowledge_no_write(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *,
        actor_user_id: UUID, expected_attempt_key: str, expected_attempt_id: str,
    ) -> object:
        service = self._service
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        await service._require_project_owner(project_id, actor_user_id)
        await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await service._locked_state(run)
        attempt = service._run_metadata(run)["provider_attempt"]
        if (
            type(attempt) is not dict
            or attempt.get("key") != expected_attempt_key
            or attempt.get("attempt_id") != expected_attempt_id
            or attempt.get("status") != "claimed"
            or attempt.get("checkpoint_index") != checkpoint.checkpoint_index
            or state.status not in {
                ChapterProductionStatus.DRAFTING,
                ChapterProductionStatus.REVIEW_REVISION,
            }
        ):
            raise ChapterProductionV2ReconciliationError() from None
        if state.status is ChapterProductionStatus.DRAFTING:
            if attempt.get("kind") != "feedback":
                raise ChapterProductionV2ReconciliationError() from None
            return await self._feedback_saga.acknowledge_no_write(
                run=run, state=state, checkpoint=checkpoint, attempt=attempt)
        if attempt.get("kind") != "review":
            raise ChapterProductionV2ReconciliationError() from None
        return await self._review_saga.acknowledge_no_write(
            run=run, state=state, checkpoint=checkpoint, attempt=attempt)


__all__ = ["ChapterDraftRevisionCoordinator"]
