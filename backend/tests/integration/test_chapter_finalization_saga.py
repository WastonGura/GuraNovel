from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chapter, Document, DocumentType, DocumentVersion, WorkflowEvent
from app.services.chapter_production_v2_service import (
    ChapterProductionV2CommitIndeterminateError,
)
from tests.integration.test_chapter_production_v2_review_service import (
    review_ready_chapter,
    run_id,
)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("fail_on_call", [1, 2, 3])
async def test_finalize_commit_ack_loss_at_each_db_commit_boundary_replays_once(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_call: int,
) -> None:
    """Each DB commit boundary in the saga must be replayable without duplicates."""

    project, chapter, owner, service, *_ = await review_ready_chapter(
        async_session, tmp_path / f"commit-ack-{fail_on_call}"
    )
    project_id = project.id
    chapter_id = chapter.id
    owner_id = owner.id
    workflow_run_id = run_id(chapter)
    for _ in range(3):
        await service.execute_current_review(
            project_id,
            chapter_id,
            workflow_run_id,
            actor_user_id=owner_id,
        )

    original_commit = service._commit
    calls = 0

    async def fail_commit_once() -> None:
        nonlocal calls
        calls += 1
        if calls == fail_on_call:
            await service._rollback()
            raise ChapterProductionV2CommitIndeterminateError()
        await original_commit()

    monkeypatch.setattr(service, "_commit", fail_commit_once)
    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await service.finalize_without_reader_panel(
            project_id,
            chapter_id,
            workflow_run_id,
            actor_user_id=owner_id,
        )
    monkeypatch.setattr(service, "_commit", original_commit)

    result = await service.finalize_without_reader_panel(
        project_id,
        chapter_id,
        workflow_run_id,
        actor_user_id=owner_id,
    )

    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.chapter_id == chapter_id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == result.final_document_id)
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == workflow_run_id,
                WorkflowEvent.event_type == "chapter_finalized",
            )
        )
        == 1
    )
    persisted_chapter = await async_session.get(Chapter, chapter_id)
    assert persisted_chapter is not None
    assert persisted_chapter.final_document_id == result.final_document_id
    assert persisted_chapter.status == "COMPLETED"
