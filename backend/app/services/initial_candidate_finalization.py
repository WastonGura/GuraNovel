from __future__ import annotations
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import app.models as db
import app.services.chapter_production_v2_contracts as contracts
import app.services.initial_bootstrap_evidence as bootstrap
import app.services.initial_provider_handoff as handoff
from app.documents.chapter_segments import (CURRENT_CHAPTER_SEGMENTER_VERSION, MAX_CHAPTER_CONTENT_BYTES, normalize_chapter_content)
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_repository import _ChapterProductionRepositoryValidationError
from app.services.chapter_production_runtime import initial_runtime_marker, persisted_runtime_pin
from app.services.initial_candidate_persistence import (InitialCandidateIdentity, InitialCandidatePersistence)
from app.services.provider_attempt_contracts import (CONTRACT_VERSION, ProviderAttemptKind, ProviderAttemptStatus, initial_operation_key)
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path
from app.workflows.chapter_production import (ChapterActionBinding, ChapterActionKind, ChapterProductionState, ChapterProductionStatus)
_ACTION_TYPE, _PROMPT, _OPTIONS = "chapter_author_revision", "Review the current chapter draft.", ["accept", "request_revision", "submit_manual_edit"]
_Invalid, _Reconcile = contracts.ChapterProductionV2ValidationError, contracts.ChapterProductionV2ReconciliationError
class InitialCandidateNotApplicable(Exception):
    pass
class InitialRecoveryRoute(Enum):
    LEGACY = "legacy"
def _validate_inputs(identity: object, actor_user_id: object) -> None:
    valid = type(identity) is InitialCandidateIdentity
    try:
        if valid:
            identity.__post_init__()
    except BaseException:
        valid = False
    if not valid or type(actor_user_id) is not UUID or actor_user_id.int == 0:
        raise _Invalid() from None
def _action_metadata(document: db.Document, version: db.DocumentVersion, operation_key: str) -> dict[str, str]:
    return {"contract_version": "chapter-production-v2", "operation_key": operation_key, "action_kind": ChapterActionKind.AUTHOR_REVISION.value,
        "document_id": str(document.id), "document_version_id": str(version.id), "content_hash": version.content_hash}
def _action_binding(action: db.ActionRequest, document: db.Document, version: db.DocumentVersion) -> ChapterActionBinding:
    return ChapterActionBinding(action_request_id=str(action.id), workflow_run_id=str(action.workflow_run_id), chapter_id=str(action.chapter_id), request_type=action.request_type,
        kind=ChapterActionKind.AUTHOR_REVISION, status=db.ActionRequestStatus.PENDING,
        pending_count=1, document_id=str(document.id), document_version_id=str(version.id), content_hash=version.content_hash,
        current_document_id=str(document.id), current_document_version_id=str(version.id), current_content_hash=version.content_hash)
def _version_metadata(value: object, initial_key: str | None = None) -> bool:
    if type(value) is not dict or set(value) != {"contract_version", "operation_key", "attempt_id"}:
        return False
    token, key = value.get("attempt_id"), value.get("operation_key")
    try:
        valid_token = type(token) is str and UUID(token).int != 0 and str(UUID(token)) == token
    except (TypeError, ValueError):
        return False
    return (valid_token and value.get("contract_version") == CONTRACT_VERSION and contracts._valid_sha256(key) and (initial_key is None or key == initial_key))
class InitialCandidateFinalizer:
    def __init__(self, phase_sessions: ChapterPhaseSessionLease, chief_editor_required: bool) -> None:
        if (type(phase_sessions) is not ChapterPhaseSessionLease or type(chief_editor_required) is not bool):
            raise _Invalid() from None
        self.phase_sessions = phase_sessions
        self.chief_editor_required = chief_editor_required
    async def finalize(self, identity: InitialCandidateIdentity, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started:
        _validate_inputs(identity, actor_user_id)
        return (await self._leased(lambda session: _FinalizationPhase(session, self.chief_editor_required).finalize(identity, actor_user_id)))[0]
    async def resume(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID | None, *, actor_user_id: UUID) -> contracts.ChapterProductionV2Started | None:
        recovered = await self._recover(project_id, chapter_id, workflow_run_id, actor_user_id)
        if recovered[1] is InitialRecoveryRoute.LEGACY:
            raise InitialCandidateNotApplicable() from None
        return recovered[0]
    async def reconcile(self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID, *, actor_user_id: UUID) -> ChapterProductionState | InitialRecoveryRoute:
        recovered = await self._recover(project_id, chapter_id, workflow_run_id, actor_user_id)
        if recovered[0] is None and recovered[1] is not InitialRecoveryRoute.LEGACY:
            raise _Reconcile() from None
        return recovered[1]  # type: ignore[return-value]
    async def _recover(self, project_id: UUID, chapter_id: UUID, run_id: UUID | None, actor_id: UUID) -> tuple[contracts.ChapterProductionV2Started | None, ChapterProductionState | InitialRecoveryRoute | None]:
        values = (project_id, chapter_id, actor_id) + (() if run_id is None else (run_id,))
        if not all(isinstance(value, UUID) and value.int != 0 for value in values):
            raise _Invalid() from None
        project_id, chapter_id, actor_id = map(UUID, map(str, values[:3]))
        run_id = None if run_id is None else UUID(str(run_id))
        return await self._leased(lambda session: _FinalizationPhase(session, self.chief_editor_required).recover(project_id, chapter_id, run_id, actor_id))
    async def _leased(self, operation: object) -> object:
        async with self.phase_sessions.lease() as session:
            try:
                result = await operation(session)  # type: ignore[operator]
                await handoff._commit(session)
                return result
            except contracts.ChapterProductionV2CommitIndeterminateError:
                raise
            except _ChapterProductionRepositoryValidationError:
                error = _Invalid()
            except (InitialCandidateNotApplicable, contracts.ChapterProductionV2ValidationError, contracts.ChapterProductionV2ReconciliationError):
                await handoff._rollback(session)
                raise
            except Exception:
                error = _Reconcile()
            await handoff._rollback(session)
            raise error from None
class _FinalizationPhase:
    def __init__(self, session: AsyncSession, chief_editor_required: bool) -> None:
        self.session = session
        self.chief_editor_required = chief_editor_required
    async def finalize(self, identity: InitialCandidateIdentity, actor_user_id: UUID) -> tuple[contracts.ChapterProductionV2Started, ChapterProductionState]:
        phase = handoff._InitialEvidencePhase(self.session, self.chief_editor_required)
        await phase.repository.require_project_owner(identity.project_id, actor_user_id, lock=True)
        chapter, outline, outline_version = await phase.repository.approved_outline(identity.project_id, identity.chapter_id, lock=True)
        await phase.documents.derive_chapter_production_segment_map(project_id=identity.project_id, chapter_id=identity.chapter_id, document_id=outline.id, version_id=outline_version.id)
        run = await phase.repository.run(identity.project_id, identity.chapter_id, identity.workflow_run_id, lock=True)
        binding = bootstrap.InitialBootstrapBinding(identity.workflow_run_id, identity.chapter_id, UUID(str(outline.id)),
            UUID(str(outline_version.id)), outline_version.content_hash, identity.operation_key, self.chief_editor_required)
        checkpoints = await self._rows(select(db.WorkflowCheckpoint).where(db.WorkflowCheckpoint.workflow_run_id == run.id).order_by(db.WorkflowCheckpoint.checkpoint_index))
        document, version = await self._candidate(identity, chapter.chapter_number)
        actions = await self._rows(select(db.ActionRequest).where(db.ActionRequest.workflow_run_id == run.id).order_by(db.ActionRequest.created_at, db.ActionRequest.id))
        gate = _Gate(binding, document, version)
        if run.status == ChapterProductionStatus.AUTHOR_REVISION.value:
            return gate.replay(run, chapter, checkpoints, actions)
        evidence = await phase.load(identity.project_id, identity.chapter_id, actor_user_id)
        attempt = evidence.attempt
        if (evidence.run.id != run.id or evidence.operation_key != identity.operation_key
            or evidence.checkpoints != checkpoints or attempt is None
            or attempt.kind is not ProviderAttemptKind.INITIAL or attempt.status is not ProviderAttemptStatus.CLAIMED
            or attempt.attempt_id != identity.attempt_id or attempt.operation_key != identity.operation_key
            or attempt.checkpoint_index != checkpoints[-1].checkpoint_index or actions
            or chapter.current_draft_document_id is not None):
            raise _Reconcile() from None
        return await gate.create(self.session, evidence.run, chapter, checkpoints[-1])
    async def _rows(self, statement: object) -> tuple[object, ...]:
        statement = statement.with_for_update().execution_options(populate_existing=True)  # type: ignore[attr-defined]
        return tuple(await self.session.scalars(statement))  # type: ignore[arg-type]
    async def recover(self, project_id: UUID, chapter_id: UUID, expected_id: UUID | None, actor_id: UUID) -> tuple[contracts.ChapterProductionV2Started | None, ChapterProductionState | InitialRecoveryRoute | None]:
        phase = handoff._InitialEvidencePhase(self.session, self.chief_editor_required)
        await phase.repository.require_project_owner(project_id, actor_id, lock=True)
        chapter, outline, version = await phase.repository.approved_outline(project_id, chapter_id, lock=True)
        segment_map = await phase.documents.derive_chapter_production_segment_map(project_id=project_id, chapter_id=chapter_id, document_id=outline.id, version_id=version.id)
        key = initial_operation_key(project_id=project_id, chapter_id=chapter_id, outline_document_id=UUID(str(outline.id)), outline_version_id=UUID(str(version.id)), outline_content_hash=version.content_hash, segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION)
        run = await phase.repository.operation_run(project_id, chapter_id, key)
        if run is None:
            if expected_id is not None:
                raise _Invalid() from None
            return None, None
        if expected_id is not None and UUID(str(run.id)) != expected_id:
            raise _Invalid() from None
        binding = bootstrap.InitialBootstrapBinding(UUID(str(run.id)), chapter_id, UUID(str(outline.id)), UUID(str(version.id)), version.content_hash, key, self.chief_editor_required)
        metadata = run.metadata_
        if type(metadata) is dict and "reviewer_claim" not in metadata:
            metadata = run.metadata_ = {**metadata, "reviewer_claim": None}
        elif type(metadata) is dict and metadata.get("reviewer_claim") is not None:
            raise InitialCandidateNotApplicable() from None
        payload = metadata.get("provider_attempt") if type(metadata) is dict else None
        routed = handoff.ProviderAttempt.from_payload(payload)
        if routed is not None and routed.kind is not ProviderAttemptKind.INITIAL:
            raise InitialCandidateNotApplicable() from None
        malformed, routed_attempt = False, None
        try:
            routed_attempt = phase._attempt(run, bootstrap.pristine_run_metadata(binding), key)
        except Exception:
            malformed = True
        if malformed:
            raise _Invalid() from None
        checkpoints = await self._rows(select(db.WorkflowCheckpoint).where(db.WorkflowCheckpoint.workflow_run_id == run.id).order_by(db.WorkflowCheckpoint.checkpoint_index))
        if (not checkpoints or tuple(item.checkpoint_index for item in checkpoints)
                != tuple(range(len(checkpoints)))):
            raise _Reconcile() from None
        latest = checkpoints[-1]
        try:
            current = ChapterProductionState.from_checkpoint(latest.state_json)
            current.validate_persistence_binding(
                workflow_run_id=str(run.id), chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type, run_status=run.status, run_current_node=run.current_node,
                run_awaiting_user=run.awaiting_user, checkpoint_workflow_run_id=str(latest.workflow_run_id),
                checkpoint_node_name=latest.node_name)
        except Exception:
            raise _Reconcile() from None
        if current.status not in {ChapterProductionStatus.DRAFTING, ChapterProductionStatus.FAILED, ChapterProductionStatus.AUTHOR_REVISION}:
            raise InitialCandidateNotApplicable() from None
        if current.document_id is not None and current.status is not ChapterProductionStatus.AUTHOR_REVISION:
            raise InitialCandidateNotApplicable() from None
        if current.status is ChapterProductionStatus.AUTHOR_REVISION:
            return await self._author_replay(
                phase, binding, run, chapter, checkpoints, current, routed_attempt, actor_id)
        evidence = await phase.load(project_id, chapter_id, actor_id)
        if evidence.run.id != run.id or evidence.segment_map.map_hash != segment_map.map_hash:
            raise _Reconcile() from None
        token = None if evidence.attempt is None else evidence.attempt.attempt_id
        candidates = await self._recover_candidates(run.id, key, token)
        if not candidates:
            if evidence.attempt is not None and evidence.attempt.status is ProviderAttemptStatus.CLAIMED:
                raise _Reconcile() from None
            return None, evidence.state
        if len(candidates) != 1:
            raise _Reconcile() from None
        identity = self._identity(project_id, chapter_id, run.id, candidates[0], key)
        return await self.finalize(identity, actor_id)
    async def _author_replay(self, phase: handoff._InitialEvidencePhase, binding: bootstrap.InitialBootstrapBinding,
        run: db.WorkflowRun, chapter: db.Chapter, checkpoints: tuple[db.WorkflowCheckpoint, ...],
        current: ChapterProductionState, attempt: object, actor_id: UUID) -> tuple[contracts.ChapterProductionV2Started | None, ChapterProductionState | InitialRecoveryRoute]:
        if attempt is not None:
            raise _Reconcile() from None
        try:
            action_id = UUID(current.action_request_id or "")
        except ValueError:
            raise _Invalid() from None
        actions = await self._rows(select(db.ActionRequest).where(db.ActionRequest.workflow_run_id == run.id).order_by(db.ActionRequest.created_at, db.ActionRequest.id))
        exact, pending = tuple(action for action in actions if action.id == action_id), tuple(action for action in actions if action.status == db.ActionRequestStatus.PENDING.value)
        if len(exact) != 1:
            raise _Reconcile() from None
        action = exact[0]
        if action.status != db.ActionRequestStatus.PENDING.value:
            manual = (not pending and action.status == db.ActionRequestStatus.REVISED.value
                and action.user_decision == "submit_manual_edit"
                and action.resolved_by_id is not None and action.user_feedback is None
                and action.resolved_at is not None and action.expires_at is None
                and action.project_id == run.project_id and action.chapter_id == binding.chapter_id
                and action.workflow_run_id == run.id and action.request_type == _ACTION_TYPE
                and action.prompt == _PROMPT and action.options == _OPTIONS
                and action.default_option == "accept" and contracts._valid_sha256(action_key := (action.metadata_.get("operation_key") if type(action.metadata_) is dict else None))
                and handoff._exact_json(action.metadata_, {"contract_version": "chapter-production-v2",
                    "operation_key": action_key, "action_kind": ChapterActionKind.AUTHOR_REVISION.value,
                    "document_id": current.document_id, "document_version_id": current.document_version_id,
                    "content_hash": current.content_hash}))
            if not manual:
                raise _Reconcile() from None
            return None, InitialRecoveryRoute.LEGACY
        if len(pending) != 1:
            raise _Reconcile() from None
        candidates = await self._recover_candidates(run.id, binding.operation_key, None)
        selected = tuple(item for item in candidates if (str(item[0].id), str(item[1].id)) == (current.document_id, current.document_version_id))
        if len(selected) != 1:
            raise _Reconcile() from None
        if selected[0][1].parent_version_id is not None:
            return await self._feedback_replay(phase, binding, run, chapter, checkpoints, selected[0], candidates)
        if len(candidates) != 1 or checkpoints[-1].checkpoint_index != 1:
            raise _Reconcile() from None
        identity = self._identity(UUID(str(run.project_id)), binding.chapter_id, run.id, candidates[0], binding.operation_key)
        if (current.document_id, current.document_version_id) != (str(identity.document_id), str(identity.version_id)):
            raise InitialCandidateNotApplicable() from None
        return await self.finalize(identity, actor_id)
    async def _feedback_replay(self, phase: handoff._InitialEvidencePhase, binding: bootstrap.InitialBootstrapBinding, run: db.WorkflowRun,
        chapter: db.Chapter, checkpoints: tuple[db.WorkflowCheckpoint, ...], selected: tuple[db.Document, db.DocumentVersion],
        candidates: list[tuple[db.Document, db.DocumentVersion]]) -> tuple[contracts.ChapterProductionV2Started, ChapterProductionState]:
        document, version = selected
        metadata = version.metadata_
        ordered = sorted(candidates, key=lambda item: item[1].version_number)
        if (len(checkpoints) < 2 or len(ordered) != version.version_number
                or ordered[-1][1].id != version.id
                or any(item_document.id != document.id or item.workflow_run_id != run.id
                    or item.version_number != index or item.parent_version_id != (None if index == 1 else ordered[index - 2][1].id)
                    or item.source != db.DocumentSource.WRITER_AGENT.value or item.actor_user_id is not None
                    or item.agent_role != ("writer_agent" if index == 1 else "revision_agent") or item.file_path != document.path
                    or item.snapshot_path != version_snapshot_path(str(document.id), index).as_posix() or not _version_metadata(item.metadata_, binding.operation_key if index == 1 else None)
                    for index, (item_document, item) in enumerate(ordered, 1))
                or document.project_id != run.project_id or document.chapter_id != binding.chapter_id
                or document.type != db.DocumentType.CHAPTER_DRAFT.value or document.current_version_id != version.id
                or chapter.current_draft_document_id != document.id
                or document.path != f"chapters/chapter-{chapter.chapter_number:04d}-{run.id}-draft.md"
                or document.title != f"Chapter {chapter.chapter_number} draft"
                or document.metadata_ != {}
                or not _version_metadata(metadata)):
            raise _Reconcile() from None
        try:
            store = MarkdownStore(Path(document.project.workspace_root))
            current = store.read_bounded(document.path, max_bytes=MAX_CHAPTER_CONTENT_BYTES)
            snapshot = store.read_bounded(version.snapshot_path or "", max_bytes=MAX_CHAPTER_CONTENT_BYTES)
        except Exception:
            raise _Reconcile() from None
        if current != snapshot:
            raise _Reconcile() from None
        await phase.documents.derive_chapter_segment_map(
            project_id=document.project_id, chapter_id=document.chapter_id,
            document_id=document.id, version_id=version.id)
        actions = await self._rows(select(db.ActionRequest).where(db.ActionRequest.workflow_run_id == run.id,
            db.ActionRequest.status == db.ActionRequestStatus.PENDING.value))
        return _Gate(binding, document, version, metadata["operation_key"]).replay(
            run, chapter, checkpoints, actions, ordered[-2][1].content_hash)
    async def _recover_candidates(self, run_id: UUID, key: str, attempt_id: UUID | None) -> list[tuple[db.Document, db.DocumentVersion]]:
        scope = SimpleNamespace(workflow_run_id=run_id, operation_key=key, attempt_id=attempt_id or UUID(int=0))
        return await InitialCandidatePersistence._candidates(self.session, SimpleNamespace(generation=SimpleNamespace(scope=scope)))
    @staticmethod
    def _identity(project_id: UUID, chapter_id: UUID, run_id: UUID, candidate: tuple[db.Document, db.DocumentVersion], key: str) -> InitialCandidateIdentity:
        document, version = candidate
        metadata = version.metadata_
        token = metadata.get("attempt_id") if type(metadata) is dict else None
        try:
            attempt_id = UUID(token) if type(token) is str and str(UUID(token)) == token else None
        except ValueError:
            attempt_id = None
        if attempt_id is None or metadata != {"contract_version": CONTRACT_VERSION,
                "operation_key": key, "attempt_id": str(attempt_id)}:
            raise _Reconcile() from None
        return InitialCandidateIdentity(project_id, chapter_id, UUID(str(run_id)),
            UUID(str(document.id)), UUID(str(version.id)), version.content_hash, key, attempt_id)
    async def _candidate(self, identity: InitialCandidateIdentity, chapter_number: int) -> tuple[db.Document, db.DocumentVersion]:
        proxy = SimpleNamespace(generation=SimpleNamespace(scope=identity))
        matches = await InitialCandidatePersistence._candidates(self.session, proxy)  # type: ignore[arg-type]
        if len(matches) != 1:
            raise _Reconcile() from None
        document, version = matches[0]
        if (document.id != identity.document_id or version.id != identity.version_id
                or version.content_hash != identity.content_hash):
            raise _Reconcile() from None
        expected_path = f"chapters/chapter-{chapter_number:04d}-{identity.workflow_run_id}-draft.md"
        try:
            store = MarkdownStore(Path(document.project.workspace_root))
            current = store.read_bounded(document.path, max_bytes=MAX_CHAPTER_CONTENT_BYTES)
            snapshot = store.read_bounded(version.snapshot_path or "", max_bytes=MAX_CHAPTER_CONTENT_BYTES)
            content = normalize_chapter_content(snapshot)
            if current != snapshot or normalize_chapter_content(current) != content:
                raise ValueError
            InitialCandidatePersistence._validate_candidate(  # type: ignore[arg-type]
                document, version, proxy, content, f"Chapter {chapter_number} draft", expected_path)
            phase = handoff._InitialEvidencePhase(self.session, self.chief_editor_required)
            segment_map = await phase.documents.derive_chapter_production_segment_map(
                project_id=identity.project_id, chapter_id=identity.chapter_id,
                document_id=identity.document_id, version_id=identity.version_id)
        except Exception:
            raise _Reconcile() from None
        if segment_map.content_hash != identity.content_hash:
            raise _Reconcile() from None
        return document, version
class _Gate:
    def __init__(self, binding: bootstrap.InitialBootstrapBinding, document: db.Document, version: db.DocumentVersion, operation_key: str | None = None) -> None:
        self.binding = binding
        self.document = document
        self.version = version
        self.operation_key = operation_key or binding.operation_key
    async def create(self, session: AsyncSession, run: object, chapter: object, checkpoint: db.WorkflowCheckpoint) -> tuple[contracts.ChapterProductionV2Started, ChapterProductionState]:
        action = db.ActionRequest(
            workflow_run_id=run.id, project_id=self.document.project_id,
            chapter_id=self.binding.chapter_id, request_type=_ACTION_TYPE,
            status=db.ActionRequestStatus.PENDING.value, prompt=_PROMPT,
            options=_OPTIONS, default_option="accept",
            metadata_=_action_metadata(self.document, self.version, self.binding.operation_key))
        session.add(action)
        await session.flush()
        state = ChapterProductionState.from_checkpoint(checkpoint.state_json).submit_draft(
            document_id=str(self.document.id), document_version_id=str(self.version.id),
            content_hash=self.version.content_hash, action=_action_binding(action, self.document, self.version))
        chapter.current_draft_document_id = self.document.id
        pinned = "chapter_production_runtime" in run.metadata_
        runtime = initial_runtime_marker(run.metadata_)
        run.metadata_ = bootstrap.pristine_run_metadata(self.binding)
        if pinned:
            if runtime is None:
                raise _Reconcile() from None
            run.metadata_["chapter_production_runtime"] = runtime
        else:
            run.metadata_.pop("chapter_production_runtime")
        run.status, run.current_node = state.status.value, state.current_node
        run.awaiting_user, run.next_node = state.awaiting_user, None
        session.add(db.WorkflowCheckpoint(
            workflow_run_id=run.id, checkpoint_index=checkpoint.checkpoint_index + 1,
            node_name=state.current_node, state_json=state.to_checkpoint()))
        return self._started(action.id), state
    def replay(self, run: object, chapter: object, checkpoints: tuple[db.WorkflowCheckpoint, ...], actions: tuple[db.ActionRequest, ...], parent_hash: str | None = None) -> tuple[contracts.ChapterProductionV2Started, ChapterProductionState]:
        expected_metadata = bootstrap.pristine_run_metadata(self.binding)
        if "chapter_production_runtime" in run.metadata_:
            expected_metadata["chapter_production_runtime"] = persisted_runtime_pin(run.metadata_)
        else:
            expected_metadata.pop("chapter_production_runtime")
        if (len(checkpoints) < 2 or len(actions) != 1
                or chapter.current_draft_document_id != self.document.id
                or not handoff._exact_json(
                    run.metadata_,
                    expected_metadata,
                )):
            raise _Reconcile() from None
        action = actions[0]
        self._validate_action(action)
        prior = ChapterProductionState.from_checkpoint(checkpoints[-2].state_json)
        if self.version.parent_version_id is None:
            prior = handoff._InitialEvidencePhase._history(SimpleNamespace(
                id=run.id, chapter_id=run.chapter_id, workflow_type=run.workflow_type,
                status=prior.status.value, current_node=prior.current_node,
                next_node=None, awaiting_user=prior.awaiting_user,
            ), checkpoints[:-1], self.binding)
        elif (prior.status is not ChapterProductionStatus.DRAFTING
              or prior.document_id != str(self.document.id)
              or prior.document_version_id != str(self.version.parent_version_id)
              or prior.content_hash != parent_hash):
            raise _Reconcile() from None
        expected = prior.submit_draft(document_id=str(self.document.id),
            document_version_id=str(self.version.id), content_hash=self.version.content_hash,
            action=_action_binding(action, self.document, self.version))
        checkpoint = checkpoints[-1]
        projection = (
            run.workflow_type, run.status, run.current_node, run.next_node,
            run.awaiting_user, checkpoint.workflow_run_id,
            checkpoint.checkpoint_index, checkpoint.node_name, checkpoint.state_json)
        expected_projection = (
            "chapter_production", expected.status.value, expected.current_node, None,
            True, run.id, checkpoints[-2].checkpoint_index + 1,
            expected.current_node, expected.to_checkpoint())
        if any(not handoff._exact_json(actual, wanted) for actual, wanted in zip(
                projection, expected_projection, strict=True)):
            raise _Reconcile() from None
        return self._started(action.id), expected
    def _validate_action(self, action: db.ActionRequest) -> None:
        if (action.workflow_run_id != self.binding.workflow_run_id
                or action.project_id != self.document.project_id
                or action.chapter_id != self.document.chapter_id
                or action.request_type != _ACTION_TYPE
                or action.status != db.ActionRequestStatus.PENDING.value
                or action.prompt != _PROMPT or action.options != _OPTIONS
                or action.default_option != "accept"
                or any(item is not None for item in (
                action.user_decision, action.user_feedback, action.resolved_by_id,
                action.resolved_at, action.expires_at))
                or not handoff._exact_json(action.metadata_, _action_metadata(
                    self.document, self.version, self.operation_key))):
            raise _Reconcile() from None
    def _started(self, action_id: UUID) -> contracts.ChapterProductionV2Started:
        return contracts.ChapterProductionV2Started(
            self.binding.workflow_run_id, action_id,
            self.binding.outline_document_id, self.binding.outline_version_id,
            self.document.id, self.version.id)
__all__ = ["InitialCandidateFinalizer", "InitialCandidateNotApplicable", "InitialRecoveryRoute"]
