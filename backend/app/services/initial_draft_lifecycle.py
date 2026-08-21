"""Compose the extracted initial draft phases without owning persistence rules."""
from uuid import UUID
from app.graph.chapter_production_execution import invoke_chapter_production_graph
from app.graph.contracts import GraphError
from app.agents.chapter_writer_agents import WriterAgent
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
import app.services.chapter_production_v2_contracts as contracts
from app.services.chapter_production_graph_domain import ChapterProductionInvocationContext
from app.services.chapter_production_graph_reconstruction import reconstruct_scheduler_input
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
    load_chapter_production_runtime,
)
from app.services.initial_candidate_finalization import InitialCandidateFinalizer, InitialCandidateNotApplicable, InitialRecoveryRoute
from app.services.initial_candidate_persistence import InitialCandidatePersistence, _InitialCandidateSourceChanged
from app.services.initial_provider_handoff import InitialProviderHandoff
from app.workflows.chapter_production import ChapterProductionState
_NOT_APPLICABLE = object()
_DOMAIN_ERRORS = (
    contracts.ChapterProductionV2ValidationError,
    contracts.ChapterProductionV2ProviderError,
    contracts.ChapterProductionV2ReviewProviderError,
    contracts.ChapterProductionV2CommitIndeterminateError,
    contracts.ChapterProductionV2ReconciliationError,
)


def _raise_reconciliation() -> None:
    error = contracts.ChapterProductionV2ReconciliationError()
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


class InitialDraftLifecycle:
    def __init__(self, service: object, sessions: ChapterPhaseSessionLease, writer: WriterAgent, chief_editor_required: bool) -> None:
        self._service = service
        self.sessions = sessions
        self.handoff = InitialProviderHandoff(sessions, writer, chief_editor_required)
        self.persistence = InitialCandidatePersistence(sessions, chief_editor_required)
        self.finalizer = InitialCandidateFinalizer(sessions, chief_editor_required)
    async def start(self, project_id: UUID, chapter_id: UUID, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        recovered = await self._replay(project_id, chapter_id, None, actor_user_id)
        if recovered is not None:
            return recovered
        return await self._start_pinned(project_id, chapter_id, actor_user_id)
    async def _start_pinned(self, project_id: UUID, chapter_id: UUID, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        run_id = await self.handoff.bootstrap.start_or_resume(
            project_id, chapter_id, actor_user_id=actor_user_id
        )
        return await self._dispatch(project_id, chapter_id, run_id, actor_user_id)
    async def resume(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        return await self._dispatch(project_id, chapter_id, workflow_run_id, actor_user_id)
    async def _dispatch(self, project_id: UUID, chapter_id: UUID, run_id: UUID, actor_id: UUID) -> contracts.ChapterProductionV2Started:
        try:
            async with self.sessions.lease() as session:
                runtime = await load_chapter_production_runtime(session, run_id)
                state = (
                    await reconstruct_scheduler_input(session, run_id)
                    if runtime == chapter_production_langgraph_pin()
                    else None
                )
        except (contracts.ChapterProductionV2ValidationError, GraphError):
            _raise_reconciliation()
        except Exception:
            _raise_reconciliation()
        if runtime is None or runtime == chapter_production_runtime_pin():
            # Missing is only a historical marker. Existing provider/finalizer
            # evidence validation must accept the exact legacy shape before work.
            return await self._run(project_id, chapter_id, run_id, actor_id)
        if state is None:
            _raise_reconciliation()
        try:
            result = await invoke_chapter_production_graph(
                self._service,
                context=ChapterProductionInvocationContext(project_id, chapter_id, actor_id),
                state=state,
            )
        except GraphError:
            _raise_reconciliation()
        except _DOMAIN_ERRORS:
            raise
        except Exception:
            _raise_reconciliation()
        if result.kind != "await-user":
            _raise_reconciliation()
        return await self._run(project_id, chapter_id, run_id, actor_id)
    async def _replay(self, project_id: UUID, chapter_id: UUID, run_id: UUID | None, actor_id: UUID) -> contracts.ChapterProductionV2Started | None:
        try:
            return await self.finalizer.resume(
                project_id, chapter_id, run_id, actor_user_id=actor_id
            )
        except InitialCandidateNotApplicable:
            raise contracts.ChapterProductionV2ValidationError() from None
    async def _run(self, project_id: UUID, chapter_id: UUID, run_id: UUID | None, actor_id: UUID) -> contracts.ChapterProductionV2Started:
        recovered = await self._replay(project_id, chapter_id, run_id, actor_id)
        if recovered is not None:
            return recovered
        result = await self.handoff.execute(project_id, chapter_id, actor_user_id=actor_id, expected_run_id=run_id)
        try:
            identity = await self.persistence.persist(result)
        except _InitialCandidateSourceChanged:
            identity = _NOT_APPLICABLE
        if identity is _NOT_APPLICABLE:
            raise contracts.ChapterProductionV2ValidationError() from None
        return await self.finalizer.finalize(identity, actor_user_id=actor_id)
    async def reconcile(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, actor_user_id: UUID) -> ChapterProductionState | InitialRecoveryRoute:
        try:
            result = await self.finalizer.reconcile(project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id)
        except InitialCandidateNotApplicable:
            result = InitialRecoveryRoute.LEGACY
        except contracts.ChapterProductionV2ValidationError:
            result = _NOT_APPLICABLE
        if result is _NOT_APPLICABLE:
            raise contracts.ChapterProductionV2ReconciliationError() from None
        return result
    async def acknowledge_no_write(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, actor_user_id: UUID, operation_key: str, attempt_id: UUID) -> ChapterProductionState:
        result = await self.handoff.acknowledge_no_write_identity(
            project_id, chapter_id, workflow_run_id, actor_user_id=actor_user_id,
            operation_key=operation_key, attempt_id=attempt_id)
        if result is None:
            raise InitialCandidateNotApplicable() from None
        return result
__all__ = ["InitialDraftLifecycle", "InitialCandidateNotApplicable"]
