"""Persist or reuse one pristine Chapter Production V2 initial run."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph import chapter_production_topology
from app.documents.chapter_segments import CURRENT_CHAPTER_SEGMENTER_VERSION
from app.models import WorkflowCheckpoint, WorkflowRun, WorkflowType
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_repository import ChapterProductionRepository
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
)
from app.services.document_service import DocumentService
from app.services.initial_bootstrap_evidence import (
    InitialBootstrapBinding,
    pristine_checkpoint,
    pristine_run_metadata,
    validate_pristine_initial_evidence,
)
from app.services.provider_attempt_contracts import CONTRACT_VERSION, initial_operation_key


_INACTIVE_STATUSES = frozenset({"COMPLETED", "CANCELLED"})


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _require_ids(*values: UUID) -> None:
    if not all(type(value) is UUID and value.int != 0 for value in values):
        raise _invalid() from None


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except BaseException:
        pass


async def _commit(session: AsyncSession) -> None:
    failed = False
    try:
        await session.commit()
    except BaseException:
        failed = True
    if failed:
        await _rollback(session)
        raise ChapterProductionV2CommitIndeterminateError() from None


class _InitialRunPhase:
    def __init__(self, session: AsyncSession, chief_editor_required: bool) -> None:
        self.session = session
        self.chief_editor_required = chief_editor_required
        self.documents = DocumentService(session)
        self.repository = ChapterProductionRepository(
            session,
            contract_version=CONTRACT_VERSION,
            inactive_run_statuses=_INACTIVE_STATUSES,
        )

    async def start_or_resume(
        self, project_id: UUID, chapter_id: UUID, actor_user_id: UUID
    ) -> UUID:
        failed = False
        try:
            run = await self._locked_run(project_id, chapter_id, actor_user_id)
            run_id = run.id
            await _commit(self.session)
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except Exception:
            failed = True
        if failed:
            await _rollback(self.session)
            raise _invalid() from None
        return run_id

    async def _locked_run(
        self, project_id: UUID, chapter_id: UUID, actor_user_id: UUID
    ) -> WorkflowRun:
        await self.repository.require_project_owner(project_id, actor_user_id, lock=True)
        _, outline, version = await self.repository.approved_outline(
            project_id, chapter_id, lock=True
        )
        await self.documents.derive_chapter_production_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=outline.id,
            version_id=version.id,
        )
        key = initial_operation_key(
            project_id=project_id,
            chapter_id=chapter_id,
            outline_document_id=UUID(str(outline.id)),
            outline_version_id=UUID(str(version.id)),
            outline_content_hash=version.content_hash,
            segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
        )
        run = await self.repository.operation_run(project_id, chapter_id, key)
        if run is None:
            return await self._create(project_id, chapter_id, outline.id, version.id, version.content_hash, key)
        await self._validate(run, chapter_id, outline.id, version.id, version.content_hash, key)
        return run

    def _binding(
        self,
        run_id: UUID,
        chapter_id: UUID,
        outline_id: UUID,
        version_id: UUID,
        content_hash: str,
        key: str,
    ) -> InitialBootstrapBinding:
        return InitialBootstrapBinding(
            workflow_run_id=UUID(str(run_id)),
            chapter_id=chapter_id,
            outline_document_id=UUID(str(outline_id)),
            outline_version_id=UUID(str(version_id)),
            outline_content_hash=content_hash,
            operation_key=key,
            chief_editor_required=self.chief_editor_required,
        )

    async def _create(
        self,
        project_id: UUID,
        chapter_id: UUID,
        outline_id: UUID,
        version_id: UUID,
        content_hash: str,
        key: str,
    ) -> WorkflowRun:
        run_id = uuid4()
        binding = self._binding(
            run_id, chapter_id, outline_id, version_id, content_hash, key
        )
        run = WorkflowRun(
            id=run_id,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
            status="DRAFTING",
            current_node="drafting",
            next_node=None,
            awaiting_user=False,
            metadata_={
                **pristine_run_metadata(binding),
                "chapter_production_runtime": (
                    chapter_production_langgraph_pin()
                    if chapter_production_topology.GRAPH_ENABLED
                    else chapter_production_runtime_pin()
                ),
            },
        )
        self.session.add(run)
        await self.session.flush()
        self.session.add(
            WorkflowCheckpoint(
                workflow_run_id=run_id,
                checkpoint_index=0,
                node_name="drafting",
                state_json=pristine_checkpoint(binding),
            )
        )
        return run

    async def _validate(
        self,
        run: WorkflowRun,
        chapter_id: UUID,
        outline_id: UUID,
        version_id: UUID,
        content_hash: str,
        key: str,
    ) -> None:
        checkpoints = tuple(
            await self.session.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run.id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        binding = self._binding(
            run.id, chapter_id, outline_id, version_id, content_hash, key
        )
        normalized = validate_pristine_initial_evidence(
            binding,
            workflow_type=run.workflow_type,
            status=run.status,
            current_node=run.current_node,
            next_node=run.next_node,
            awaiting_user=run.awaiting_user,
            metadata=run.metadata_,
            checkpoint_markers=tuple(
                (item.checkpoint_index, item.node_name, item.state_json)
                for item in checkpoints
            ),
        )
        if run.metadata_ != normalized:
            run.metadata_ = normalized


class InitialRunBootstrap:
    def __init__(
        self, phase_sessions: ChapterPhaseSessionLease, chief_editor_required: bool
    ) -> None:
        if (
            type(phase_sessions) is not ChapterPhaseSessionLease
            or type(chief_editor_required) is not bool
        ):
            raise _invalid() from None
        self.phase_sessions = phase_sessions
        self.chief_editor_required = chief_editor_required

    async def start_or_resume(
        self, project_id: UUID, chapter_id: UUID, *, actor_user_id: UUID
    ) -> UUID:
        _require_ids(project_id, chapter_id, actor_user_id)
        async with self.phase_sessions.lease() as session:
            return await _InitialRunPhase(
                session, self.chief_editor_required
            ).start_or_resume(project_id, chapter_id, actor_user_id)


__all__ = ["InitialRunBootstrap"]
