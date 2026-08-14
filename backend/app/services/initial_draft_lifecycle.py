"""Compose the extracted initial draft phases without owning persistence rules."""
from uuid import UUID
from app.agents.chapter_writer_agents import WriterAgent
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
import app.services.chapter_production_v2_contracts as contracts
from app.services.initial_candidate_finalization import InitialCandidateFinalizer, InitialCandidateNotApplicable, InitialRecoveryRoute
from app.services.initial_candidate_persistence import InitialCandidatePersistence, _InitialCandidateSourceChanged
from app.services.initial_provider_handoff import InitialProviderHandoff
from app.workflows.chapter_production import ChapterProductionState
_NOT_APPLICABLE = object()
class InitialDraftLifecycle:
    def __init__(self, sessions: ChapterPhaseSessionLease, writer: WriterAgent, chief_editor_required: bool) -> None:
        self.handoff = InitialProviderHandoff(sessions, writer, chief_editor_required)
        self.persistence = InitialCandidatePersistence(sessions, chief_editor_required)
        self.finalizer = InitialCandidateFinalizer(sessions, chief_editor_required)
    async def start(self, project_id: UUID, chapter_id: UUID, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        return await self._run(project_id, chapter_id, None, actor_user_id)
    async def resume(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        return await self._run(project_id, chapter_id, workflow_run_id, actor_user_id)
    async def _run(self, project_id: UUID, chapter_id: UUID, run_id: UUID | None, actor_id: UUID) -> contracts.ChapterProductionV2Started:
        try:
            recovered = await self.finalizer.resume(project_id, chapter_id, run_id, actor_user_id=actor_id)
        except InitialCandidateNotApplicable:
            recovered = _NOT_APPLICABLE
        if recovered is _NOT_APPLICABLE:
            raise contracts.ChapterProductionV2ValidationError() from None
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
