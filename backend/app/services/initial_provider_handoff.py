"""Initial claim and session-free Writer provider handoff."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chapter_writer_agents import WriterAgent
from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    CandidateChapterOutput,
    InitialDraftRequest,
)
from app.documents.chapter_segments import CURRENT_CHAPTER_SEGMENTER_VERSION
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.models import WorkflowCheckpoint, WorkflowRun
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_repository import (
    ChapterProductionRepository,
    _ChapterProductionRepositoryValidationError,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentService
from app.services.initial_bootstrap_evidence import (
    InitialBootstrapBinding,
    pristine_checkpoint,
    pristine_run_metadata,
)
from app.services.initial_generation_snapshot import (
    InitialGenerationScope,
    InitialGenerationSnapshot,
)
from app.services.initial_request_snapshot import validate_initial_request_snapshot
from app.services.initial_run_bootstrap import InitialRunBootstrap
from app.services.provider_attempt_contracts import (
    CONTRACT_VERSION,
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderAttemptStatus,
    initial_operation_key,
    new_attempt_id,
)
from app.services.provider_attempt_store import ProviderAttemptScope, ProviderAttemptStore
from app.workflows.chapter_production import (
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
)


_INACTIVE = frozenset({"COMPLETED", "CANCELLED"})
_PROVIDER_FAILURES = (
    ChapterFailureCode.PROVIDER_TIMEOUT,
    ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
    ChapterFailureCode.PROVIDER_UNAVAILABLE,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconcile() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


def _exact_json(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        if set(actual) != set(expected):
            return False
        return all(_exact_json(actual[key], value) for key, value in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _exact_json(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


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


def _append_state(
    session: AsyncSession, run: WorkflowRun, checkpoint_index: int,
    state: ChapterProductionState,
) -> None:
    run.status = state.status.value
    run.current_node = state.current_node
    run.awaiting_user = state.awaiting_user
    run.next_node = None
    session.add(WorkflowCheckpoint(
        workflow_run_id=run.id, checkpoint_index=checkpoint_index,
        node_name=state.current_node, state_json=state.to_checkpoint(),
    ))


@dataclass(frozen=True, slots=True, repr=False)
class InitialProviderResult:
    generation: InitialGenerationSnapshot
    request: InitialDraftRequest = field(repr=False)
    candidate: CandidateChapterOutput = field(repr=False)

    @property
    def workflow_run_id(self) -> UUID:
        return self.generation.scope.workflow_run_id

    def __repr__(self) -> str:
        return "InitialProviderResult()"


@dataclass(slots=True, repr=False)
class _Evidence:
    run: WorkflowRun
    checkpoints: tuple[WorkflowCheckpoint, ...]
    state: ChapterProductionState
    attempt: ProviderAttempt | None
    operation_key: str
    segment_map: object = field(repr=False)


class _InitialEvidencePhase:
    def __init__(self, session: AsyncSession, chief_editor_required: bool) -> None:
        self.session = session
        self.chief_editor_required = chief_editor_required
        self.documents = DocumentService(session)
        self.repository = ChapterProductionRepository(
            session, contract_version=CONTRACT_VERSION,
            inactive_run_statuses=_INACTIVE,
        )
        self.attempts = ProviderAttemptStore(session, self.repository)

    async def load(
        self, project_id: UUID, chapter_id: UUID, actor_user_id: UUID
    ) -> _Evidence:
        await self.repository.require_project_owner(project_id, actor_user_id, lock=True)
        _, outline, version = await self.repository.approved_outline(
            project_id, chapter_id, lock=True
        )
        segment_map = await self.documents.derive_chapter_production_segment_map(
            project_id=project_id, chapter_id=chapter_id,
            document_id=outline.id, version_id=version.id,
        )
        key = initial_operation_key(
            project_id=project_id, chapter_id=chapter_id,
            outline_document_id=UUID(str(outline.id)),
            outline_version_id=UUID(str(version.id)),
            outline_content_hash=version.content_hash,
            segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
        )
        run = await self.repository.operation_run(project_id, chapter_id, key)
        if run is None:
            raise _reconcile() from None
        checkpoints = tuple(await self.session.scalars(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index)
            .with_for_update().execution_options(populate_existing=True)
        ))
        binding = InitialBootstrapBinding(
            UUID(str(run.id)), chapter_id, UUID(str(outline.id)), UUID(str(version.id)),
            version.content_hash, key, self.chief_editor_required,
        )
        attempt = self._attempt(run, pristine_run_metadata(binding), key)
        state = self._history(run, checkpoints, binding)
        self._align(attempt, state, checkpoints[-1].checkpoint_index, key)
        return _Evidence(run, checkpoints, state, attempt, key, segment_map)

    @staticmethod
    def _attempt(
        run: WorkflowRun, expected: dict[str, object], key: str
    ) -> ProviderAttempt | None:
        if type(run.metadata_) is not dict:
            raise _reconcile() from None
        payload = run.metadata_.get("provider_attempt")
        attempt = None if payload is None else ProviderAttempt.from_payload(payload)
        if (
            (payload is not None and attempt is None)
            or not _exact_json(run.metadata_, {**expected, "provider_attempt": payload})
            or (attempt is not None and attempt.operation_key != key)
        ):
            raise _reconcile() from None
        return attempt

    @staticmethod
    def _history(
        run: WorkflowRun, checkpoints: tuple[WorkflowCheckpoint, ...],
        binding: InitialBootstrapBinding,
    ) -> ChapterProductionState:
        pristine = pristine_checkpoint(binding)
        failures = tuple(
            ChapterProductionState.from_checkpoint(pristine).fail(code).to_checkpoint()
            for code in _PROVIDER_FAILURES
        )
        if (
            not checkpoints or run.next_node is not None
            or tuple(item.checkpoint_index for item in checkpoints)
            != tuple(range(len(checkpoints)))
        ):
            raise _reconcile() from None
        for index, checkpoint in enumerate(checkpoints):
            payload = checkpoint.state_json
            valid = (
                _exact_json(payload, pristine)
                if index % 2 == 0
                else any(_exact_json(payload, failure) for failure in failures)
            )
            node = "drafting" if index % 2 == 0 else "failed"
            if not valid or checkpoint.node_name != node:
                raise _reconcile() from None
        state = ChapterProductionState.from_checkpoint(checkpoints[-1].state_json)
        state.validate_persistence_binding(
            workflow_run_id=str(run.id), chapter_id=str(run.chapter_id),
            run_workflow_type=run.workflow_type, run_status=run.status,
            run_current_node=run.current_node, run_awaiting_user=run.awaiting_user,
            checkpoint_workflow_run_id=str(checkpoints[-1].workflow_run_id),
            checkpoint_node_name=checkpoints[-1].node_name,
        )
        return state

    @staticmethod
    def _align(
        attempt: ProviderAttempt | None, state: ChapterProductionState,
        checkpoint_index: int, key: str,
    ) -> None:
        if attempt is None:
            if state.status is not ChapterProductionStatus.DRAFTING:
                raise _reconcile() from None
            return
        if attempt.kind is not ProviderAttemptKind.INITIAL or attempt.operation_key != key:
            raise _reconcile() from None
        expected = checkpoint_index if attempt.status is ProviderAttemptStatus.CLAIMED else checkpoint_index - 1
        expected_state = (
            ChapterProductionStatus.DRAFTING
            if attempt.status is ProviderAttemptStatus.CLAIMED
            else ChapterProductionStatus.FAILED
        )
        if attempt.checkpoint_index != expected or state.status is not expected_state:
            raise _reconcile() from None

    def request(self, evidence: _Evidence) -> InitialDraftRequest:
        run_id = UUID(str(evidence.run.id))
        segment_map = evidence.segment_map
        return validate_initial_request_snapshot(InitialDraftRequest(
            project_id=UUID(str(evidence.run.project_id)),
            chapter_id=UUID(str(evidence.run.chapter_id)), workflow_run_id=run_id,
            approved_outline=ApprovedOutlineReference(
                project_id=UUID(str(evidence.run.project_id)),
                chapter_id=UUID(str(evidence.run.chapter_id)),
                document_id=UUID(str(segment_map.document_id)),
                version_id=UUID(str(segment_map.version_id)),
            ),
            allowed_segments=tuple(AllowedChapterSegment(
                segment_id=UUID(str(item.segment_id)), index=item.ordinal,
                title=item.structural_path, brief=item.content,
            ) for item in segment_map.segments),
        ))

    @staticmethod
    def store_scope(
        scope: InitialGenerationScope,
    ) -> ProviderAttemptScope:
        return ProviderAttemptScope(
            scope.project_id, scope.chapter_id, scope.workflow_run_id,
            ProviderAttemptKind.INITIAL, scope.operation_key,
            scope.checkpoint_index, scope.attempt_id,
        )


class InitialProviderHandoff:
    def __init__(
        self, phase_sessions: ChapterPhaseSessionLease, writer_agent: WriterAgent,
        chief_editor_required: bool,
    ) -> None:
        if (
            type(phase_sessions) is not ChapterPhaseSessionLease
            or type(writer_agent) is not WriterAgent
            or type(chief_editor_required) is not bool
        ):
            raise _invalid() from None
        self.phase_sessions = phase_sessions
        self.writer_agent = writer_agent
        self.chief_editor_required = chief_editor_required
        self.bootstrap = InitialRunBootstrap(phase_sessions, chief_editor_required)

    async def execute(
        self, project_id: UUID, chapter_id: UUID, *, actor_user_id: UUID
    ) -> InitialProviderResult:
        self._ids(project_id, chapter_id, actor_user_id)
        try:
            await self.bootstrap.start_or_resume(
                project_id, chapter_id, actor_user_id=actor_user_id
            )
        except ChapterProductionV2ValidationError:
            pass
        generation, request = await self._claim(project_id, chapter_id, actor_user_id)
        cancellation: asyncio.CancelledError | None = None
        failure: ChapterFailureCode | None = None
        try:
            candidate = await self.writer_agent.initial_draft(request)
        except asyncio.CancelledError:
            cancellation = asyncio.CancelledError()
        except ProviderTimeoutError:
            failure = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            failure = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except (ProviderConfigurationError, ProviderRateLimitedError, ProviderUnavailableError):
            failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        except Exception:
            failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await self._cleanup(generation, failure=None, acknowledge=False)
            raise cancellation from None
        if failure is not None:
            await self._cleanup(generation, failure=failure, acknowledge=False)
            raise ChapterProductionV2ProviderError() from None
        return InitialProviderResult(generation, request, candidate)

    async def acknowledge_no_write(self, generation: InitialGenerationSnapshot) -> None:
        if type(generation) is not InitialGenerationSnapshot:
            raise _invalid() from None
        await self._cleanup(generation, failure=None, acknowledge=True)

    async def _claim(
        self, project_id: UUID, chapter_id: UUID, actor_user_id: UUID
    ) -> tuple[InitialGenerationSnapshot, InitialDraftRequest]:
        async with self.phase_sessions.lease() as session:
            try:
                phase = _InitialEvidencePhase(session, self.chief_editor_required)
                evidence = await phase.load(project_id, chapter_id, actor_user_id)
                if evidence.attempt is not None:
                    if evidence.attempt.status is not ProviderAttemptStatus.FAILED:
                        raise _reconcile() from None
                    old_scope = InitialGenerationScope(
                        project_id, chapter_id, UUID(str(evidence.run.id)), actor_user_id,
                        evidence.operation_key, evidence.attempt.checkpoint_index,
                        evidence.attempt.attempt_id,
                    )
                    if not await phase.attempts.recover_failed(phase.store_scope(old_scope)):
                        raise _reconcile() from None
                    next_index = evidence.checkpoints[-1].checkpoint_index + 1
                    _append_state(session, evidence.run, next_index, evidence.state.recover())
                else:
                    next_index = evidence.checkpoints[-1].checkpoint_index
                attempt = ProviderAttempt.initial(
                    attempt_id=new_attempt_id(), operation_key=evidence.operation_key,
                    checkpoint_index=next_index,
                )
                scope = InitialGenerationScope(
                    project_id, chapter_id, UUID(str(evidence.run.id)), actor_user_id,
                    evidence.operation_key, next_index, attempt.attempt_id,
                )
                await phase.attempts.claim(phase.store_scope(scope), attempt)
                request_failed = False
                try:
                    request = phase.request(evidence)
                except Exception:
                    request_failed = True
                if request_failed:
                    raise ChapterProductionV2ProviderError() from None
                await _commit(session)
                return InitialGenerationSnapshot(scope, attempt), request
            except ChapterProductionV2CommitIndeterminateError:
                raise
            except _ChapterProductionRepositoryValidationError:
                await _rollback(session)
                raise _invalid() from None
            except (
                ChapterProductionV2ProviderError,
                ChapterProductionV2ValidationError,
                ChapterProductionV2ReconciliationError,
            ):
                await _rollback(session)
                raise
            except Exception:
                await _rollback(session)
                raise _reconcile() from None

    async def _cleanup(
        self, generation: InitialGenerationSnapshot,
        *, failure: ChapterFailureCode | None, acknowledge: bool,
    ) -> None:
        async with self.phase_sessions.lease() as session:
            try:
                phase = _InitialEvidencePhase(session, self.chief_editor_required)
                scope = generation.scope
                evidence = await phase.load(
                    scope.project_id, scope.chapter_id, scope.actor_user_id
                )
                if evidence.attempt != generation.attempt:
                    raise _reconcile() from None
                store_scope = phase.store_scope(scope)
                if failure is not None:
                    if await phase.attempts.mark_failed(store_scope) is None:
                        raise _reconcile() from None
                    _append_state(
                        session, evidence.run, scope.checkpoint_index + 1,
                        evidence.state.fail(failure),
                    )
                elif acknowledge:
                    await phase.attempts.acknowledge_no_write(store_scope)
                elif not await phase.attempts.release(store_scope):
                    raise _reconcile() from None
                await _commit(session)
            except ChapterProductionV2CommitIndeterminateError:
                raise
            except _ChapterProductionRepositoryValidationError:
                await _rollback(session)
                raise _invalid() from None
            except (ChapterProductionV2ValidationError, ChapterProductionV2ReconciliationError):
                await _rollback(session)
                raise
            except Exception:
                await _rollback(session)
                raise _reconcile() from None

    @staticmethod
    def _ids(*values: UUID) -> None:
        if not all(type(value) is UUID and value.int != 0 for value in values):
            raise _invalid() from None


__all__ = ["InitialProviderHandoff", "InitialProviderResult"]
