from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import asyncpg
import pytest

import app.services.initial_draft_lifecycle as lifecycle_module
from app.graph import chapter_production_topology
from app.graph.contracts import GraphError
from app.services.chapter_production_graph_domain import (
    ChapterProductionSchedulingResult,
)
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    chapter_production_runtime_pin,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Started,
    ChapterProductionV2ValidationError,
)
from app.services.initial_bootstrap_evidence import (
    InitialBootstrapBinding,
    pristine_checkpoint,
    pristine_run_metadata,
)
from app.services.initial_candidate_finalization import _Gate
from app.services.initial_draft_lifecycle import (
    InitialCandidateNotApplicable,
    InitialDraftLifecycle,
)
from app.services.initial_provider_handoff import _InitialEvidencePhase
from app.services.initial_run_bootstrap import _InitialRunPhase


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("44444444-4444-4444-8444-444444444444")
OUTLINE_ID = UUID("55555555-5555-4555-8555-555555555555")
OUTLINE_VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
DRAFT_ID = UUID("77777777-7777-4777-8777-777777777777")
DRAFT_VERSION_ID = UUID("88888888-8888-4888-8888-888888888888")
ACTION_ID = UUID("99999999-9999-4999-8999-999999999999")
HASH = "a" * 64
KEY = "b" * 64


def binding(run_id: UUID = RUN_ID) -> InitialBootstrapBinding:
    return InitialBootstrapBinding(
        run_id,
        CHAPTER_ID,
        OUTLINE_ID,
        OUTLINE_VERSION_ID,
        HASH,
        KEY,
        True,
    )


def metadata(runtime: dict[str, str]) -> dict[str, object]:
    return {
        **pristine_run_metadata(binding()),
        "chapter_production_runtime": runtime,
    }


def started() -> ChapterProductionV2Started:
    return ChapterProductionV2Started(
        RUN_ID,
        ACTION_ID,
        OUTLINE_ID,
        OUTLINE_VERSION_ID,
        DRAFT_ID,
        DRAFT_VERSION_ID,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("enabled", "expected"),
    ((False, chapter_production_runtime_pin()), (True, chapter_production_langgraph_pin())),
)
async def test_selector_pins_only_created_runs(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    expected: dict[str, str],
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", enabled)
    added: list[object] = []
    phase = object.__new__(_InitialRunPhase)
    phase.session = SimpleNamespace(add=added.append, flush=AsyncMock())
    phase.chief_editor_required = True

    run = await phase._create(
        PROJECT_ID, CHAPTER_ID, OUTLINE_ID, OUTLINE_VERSION_ID, HASH, KEY
    )

    assert run.metadata_["chapter_production_runtime"] == expected
    assert added[0] is run


def test_graph_pin_survives_initial_claim_validation() -> None:
    expected = pristine_run_metadata(binding())
    run = SimpleNamespace(metadata_=metadata(chapter_production_langgraph_pin()))

    assert _InitialEvidencePhase._attempt(run, expected, KEY) is None
    assert run.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()


def test_historical_unpinned_claim_is_service_owned_and_not_rewritten() -> None:
    expected = pristine_run_metadata(binding())
    expected.pop("chapter_production_runtime")
    run = SimpleNamespace(metadata_=dict(expected))

    assert _InitialEvidencePhase._attempt(run, pristine_run_metadata(binding()), KEY) is None
    assert run.metadata_ == expected


def test_malformed_unpinned_claim_does_not_downgrade_to_service() -> None:
    metadata_without_pin = pristine_run_metadata(binding())
    metadata_without_pin.pop("chapter_production_runtime")
    metadata_without_pin.pop("operation_key")
    run = SimpleNamespace(metadata_=metadata_without_pin)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        _InitialEvidencePhase._attempt(run, pristine_run_metadata(binding()), KEY)


@pytest.mark.anyio
async def test_finalizer_clears_attempt_without_rewriting_graph_pin() -> None:
    run_metadata = metadata(chapter_production_langgraph_pin())
    run_metadata["provider_attempt"] = {"claimed": True}
    run = SimpleNamespace(
        id=RUN_ID,
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        metadata_=run_metadata,
    )
    chapter = SimpleNamespace(current_draft_document_id=None)
    document = SimpleNamespace(id=DRAFT_ID, project_id=PROJECT_ID, chapter_id=CHAPTER_ID)
    version = SimpleNamespace(id=DRAFT_VERSION_ID, content_hash=HASH)
    checkpoint = SimpleNamespace(
        checkpoint_index=0,
        state_json=pristine_checkpoint(binding()),
    )
    added: list[object] = []

    def add(value: object) -> None:
        if value.__class__.__name__ == "ActionRequest":
            value.id = ACTION_ID  # type: ignore[attr-defined]
        added.append(value)

    session = SimpleNamespace(add=Mock(side_effect=add), flush=AsyncMock())

    await _Gate(binding(), document, version).create(session, run, chapter, checkpoint)

    assert run.metadata_["provider_attempt"] is None
    assert run.metadata_["chapter_production_runtime"] == chapter_production_langgraph_pin()
    assert len(added) == 2


@pytest.mark.anyio
async def test_finalizer_preserves_historical_missing_runtime_pin() -> None:
    run_metadata = pristine_run_metadata(binding())
    run_metadata.pop("chapter_production_runtime")
    run = SimpleNamespace(
        id=RUN_ID,
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        metadata_=run_metadata,
    )
    chapter = SimpleNamespace(current_draft_document_id=None)
    document = SimpleNamespace(id=DRAFT_ID, project_id=PROJECT_ID, chapter_id=CHAPTER_ID)
    version = SimpleNamespace(id=DRAFT_VERSION_ID, content_hash=HASH)
    checkpoint = SimpleNamespace(checkpoint_index=0, state_json=pristine_checkpoint(binding()))

    def add(value: object) -> None:
        if value.__class__.__name__ == "ActionRequest":
            value.id = ACTION_ID  # type: ignore[attr-defined]

    session = SimpleNamespace(add=Mock(side_effect=add), flush=AsyncMock())

    await _Gate(binding(), document, version).create(session, run, chapter, checkpoint)

    assert "chapter_production_runtime" not in run.metadata_


class _Lease:
    def __init__(self, marker: dict[str, bool]) -> None:
        self.marker = marker
        self.session = SimpleNamespace()

    async def __aenter__(self) -> object:
        self.marker["active"] = True
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        self.marker["active"] = False


def lifecycle(marker: dict[str, bool]) -> InitialDraftLifecycle:
    value = object.__new__(InitialDraftLifecycle)
    value._service = SimpleNamespace()
    value.sessions = SimpleNamespace(lease=lambda: _Lease(marker))
    value.handoff = SimpleNamespace(
        bootstrap=SimpleNamespace(start_or_resume=AsyncMock(return_value=RUN_ID))
    )
    value.finalizer = SimpleNamespace(resume=AsyncMock(return_value=None))
    value._run = AsyncMock(return_value=started())  # type: ignore[method-assign]
    return value


@pytest.mark.anyio
async def test_default_service_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_runtime_pin()),
    )

    result = await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert result == started()
    value._run.assert_awaited_once_with(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)
    value.handoff.bootstrap.start_or_resume.assert_awaited_once()


@pytest.mark.anyio
async def test_start_replays_existing_author_gate_before_pristine_bootstrap() -> None:
    value = lifecycle({"active": False})
    value.finalizer.resume.return_value = started()

    result = await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert result == started()
    value.finalizer.resume.assert_awaited_once_with(
        PROJECT_ID, CHAPTER_ID, None, actor_user_id=ACTOR_ID
    )
    value.handoff.bootstrap.start_or_resume.assert_not_awaited()
    value._run.assert_not_awaited()


@pytest.mark.anyio
async def test_enabled_start_dispatches_fresh_graph_pin_after_read_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    marker = {"active": False}
    value = lifecycle(marker)
    graph_state = {"workflow_run_id": RUN_ID}
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value=graph_state),
    )

    async def invoke(*_args: object, **_kwargs: object) -> object:
        assert marker["active"] is False
        return ChapterProductionSchedulingResult(
            kind="await-user", action_request_id=ACTION_ID
        )

    graph = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", graph)

    result = await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert result == started()
    graph.assert_awaited_once()
    value._run.assert_awaited_once_with(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)


@pytest.mark.anyio
async def test_enabled_start_keeps_existing_service_pin_on_service_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", True)
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_runtime_pin()),
    )
    graph = AsyncMock()
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", graph)

    result = await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert result == started()
    graph.assert_not_awaited()
    value._run.assert_awaited_once_with(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)


@pytest.mark.anyio
async def test_graph_pin_failure_never_falls_back_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value={"workflow_run_id": RUN_ID}),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "invoke_chapter_production_graph",
        AsyncMock(side_effect=GraphError()),
    )

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    value._run.assert_not_awaited()


@pytest.mark.anyio
async def test_graph_reconstruction_failure_is_fixed_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(side_effect=GraphError()),
    )

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    value._run.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ("load", "reconstruct", "invoke"))
async def test_unexpected_graph_runtime_failure_is_fixed_and_content_free(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    value = lifecycle({"active": False})
    load = AsyncMock(return_value=chapter_production_langgraph_pin())
    reconstruct = AsyncMock(return_value={"workflow_run_id": RUN_ID})
    invoke = AsyncMock(
        return_value=ChapterProductionSchedulingResult(
            kind="await-user", action_request_id=ACTION_ID
        )
    )
    {"load": load, "reconstruct": reconstruct, "invoke": invoke}[stage].side_effect = (
        RuntimeError("canary")
    )
    monkeypatch.setattr(lifecycle_module, "load_chapter_production_runtime", load)
    monkeypatch.setattr(lifecycle_module, "reconstruct_scheduler_input", reconstruct)
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", invoke)

    with pytest.raises(ChapterProductionV2ReconciliationError) as captured:
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "canary" not in repr(captured.value)
    value._run.assert_not_awaited()


@pytest.mark.anyio
async def test_graph_dispatch_preserves_domain_error_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value={"workflow_run_id": RUN_ID}),
    )
    invoke = AsyncMock(side_effect=ChapterProductionV2ProviderError())
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", invoke)
    with pytest.raises(ChapterProductionV2ProviderError):
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)

    invoke.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await value.start(PROJECT_ID, CHAPTER_ID, actor_user_id=ACTOR_ID)


@pytest.mark.anyio
async def test_resume_uses_existing_graph_pin_even_when_flag_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chapter_production_topology, "GRAPH_ENABLED", False)
    value = lifecycle({"active": False})
    monkeypatch.setattr(
        lifecycle_module,
        "load_chapter_production_runtime",
        AsyncMock(return_value=chapter_production_langgraph_pin()),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "reconstruct_scheduler_input",
        AsyncMock(return_value={"workflow_run_id": RUN_ID}),
    )
    graph = AsyncMock(
        return_value=ChapterProductionSchedulingResult(
            kind="await-user", action_request_id=ACTION_ID
        )
    )
    monkeypatch.setattr(lifecycle_module, "invoke_chapter_production_graph", graph)

    result = await value.resume(
        PROJECT_ID, CHAPTER_ID, RUN_ID, actor_user_id=ACTOR_ID
    )

    assert result == started()
    graph.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatch_canonicalizes_pg_uuid_before_runtime_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = lifecycle({"active": False})
    load = AsyncMock(return_value=chapter_production_runtime_pin())
    monkeypatch.setattr(lifecycle_module, "load_chapter_production_runtime", load)
    pg_run_id = asyncpg.pgproto.pgproto.UUID(RUN_ID.hex)

    result = await value.resume(
        PROJECT_ID, CHAPTER_ID, pg_run_id, actor_user_id=ACTOR_ID  # type: ignore[arg-type]
    )

    assert result == started()
    assert load.await_args.args[1] == RUN_ID
    assert type(load.await_args.args[1]) is UUID
    value._run.assert_awaited_once_with(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)


@pytest.mark.anyio
async def test_dispatch_rejects_hostile_uuid_without_leaking_or_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = lifecycle({"active": False})
    load = AsyncMock(return_value=chapter_production_runtime_pin())
    monkeypatch.setattr(lifecycle_module, "load_chapter_production_runtime", load)

    class Hostile:
        @property
        def int(self) -> int:
            raise RuntimeError("canary")

        def __str__(self) -> str:
            raise RuntimeError("canary")

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        await value.resume(
            PROJECT_ID, CHAPTER_ID, Hostile(), actor_user_id=ACTOR_ID  # type: ignore[arg-type]
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "canary" not in repr(captured.value)
    load.assert_not_awaited()


@pytest.mark.anyio
async def test_replay_maps_not_applicable_to_fixed_validation() -> None:
    value = lifecycle({"active": False})
    value.finalizer.resume.side_effect = InitialCandidateNotApplicable("canary")

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        await value._replay(PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "canary" not in repr(captured.value)
