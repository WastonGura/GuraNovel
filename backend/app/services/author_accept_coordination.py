"""Coordinate author-accept resolution for Chapter Production V2.

The coordinator owns the frozen #114/#115 accept transition (single pending
author gate resolving to APPROVED with a fresh checkpoint) plus the expiry
contract: one PostgreSQL clock_timestamp() value, taken after every required
lock is held, authorizes resolution only while database_now < expires_at.
It makes zero provider calls and owns no persistence rules beyond the accept
path; every write reuses the facade's locked helpers through the service.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.models import ActionRequestStatus
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import ChapterActionDecision


class _StaleActionAdopted(Exception):
    """A committed direct-user child adoption replaced the stale gate."""

    def __init__(self, result: ChapterProductionV2Updated) -> None:
        self.result = result
        super().__init__()


def _expiry_precludes_resolution(expires_at: object, database_now: object) -> bool:
    """Fail closed once the single database clock reaches the action expiry."""
    return expires_at is not None and database_now >= expires_at


class AuthorAcceptCoordinator:
    """Resolve the author accept gate inside the facade's locked transaction."""

    def __init__(self, service: object) -> None:
        self.service = service

    async def accept(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        actor_user_id: UUID,
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
            # USER edit (old action cancelled, child adopted) rather than an
            # APPROVED resolution of this gate, so the expiry check below is
            # intentionally skipped for this committed adoption path.
            return adopted.result
        database_now = await service.session.scalar(select(func.clock_timestamp()))
        if _expiry_precludes_resolution(context.action.expires_at, database_now):
            raise ChapterProductionV2ValidationError() from None
        next_state = context.state.resolve_action(
            action=context.binding,
            decision=ChapterActionDecision.ACCEPT,
        )
        service._resolve_action_row(
            context.action,
            status=ActionRequestStatus.APPROVED,
            decision=ChapterActionDecision.ACCEPT,
            actor_user_id=actor_user_id,
        )
        service._append_state(context.run, context.checkpoint, next_state)
        await service._commit()
        return ChapterProductionV2Updated(
            workflow_run_id=context.run.id,
            draft_document_id=context.document.id,
            draft_version_id=context.version.id,
            action_request_id=None,
        )


__all__ = ["AuthorAcceptCoordinator", "_StaleActionAdopted"]
