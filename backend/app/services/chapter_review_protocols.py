"""Typed facade surface used by the extracted chapter review modules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import (
    ChiefEditorChapterFinalAgent,
    EditorAgent,
    LoreChapterFinalAgent,
)
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.document_service import DocumentService
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterProductionState,
    ChapterReviewStage,
)


@runtime_checkable
class ChapterReviewService(Protocol):
    """The exact facade helpers the review coordinator and its phase modules use."""

    session: AsyncSession
    documents: DocumentService
    editor_agent: EditorAgent | None
    chief_editor_agent: ChiefEditorChapterFinalAgent | None
    lore_agent: LoreChapterFinalAgent | None

    def _validated_ids(self, *values: UUID) -> tuple[UUID, ...]: ...
    async def _require_project_owner(self, project_id: UUID, actor_user_id: UUID) -> None: ...
    async def _chapter(self, project_id: UUID, chapter_id: UUID, *, lock: bool) -> Chapter: ...
    async def _run(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, lock: bool
    ) -> WorkflowRun: ...
    async def _locked_state(
        self, run: WorkflowRun
    ) -> tuple[ChapterProductionState, WorkflowCheckpoint]: ...
    def _run_metadata(self, run: WorkflowRun) -> dict[str, str]: ...
    async def _commit(self) -> None: ...
    async def _rollback(self) -> None: ...
    async def _locked_review_document(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        state: ChapterProductionState,
        chapter: Chapter,
    ) -> tuple[Document, DocumentVersion]: ...
    async def _exact_review_report_count(
        self, *, run: WorkflowRun, version: DocumentVersion, stage: ChapterReviewStage
    ) -> int: ...
    async def _outline_for_chapter(
        self, chapter: Chapter, project_id: UUID, *, lock: bool
    ) -> tuple[Document, DocumentVersion]: ...
    def _validate_outline_metadata(
        self,
        metadata: dict[str, str],
        outline: Document,
        outline_version: DocumentVersion,
    ) -> None: ...
    def _verified_snapshot_content(self, document: Document, version: DocumentVersion) -> str: ...
    def _append_state(
        self,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState,
    ) -> None: ...
    def _resolve_action_row(
        self,
        action: ActionRequest,
        *,
        status: ActionRequestStatus,
        decision: ChapterActionDecision,
        actor_user_id: UUID,
    ) -> None: ...
    async def _enter_revision_ready_locked(
        self,
        *,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState,
        document: Document,
        version: DocumentVersion,
    ) -> ChapterProductionState: ...
