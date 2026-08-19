from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END

from app.graph import GRAPH_ID, GRAPH_VERSION, build_config
from app.graph.contracts import GraphError
from app.graph.runtime import GraphDefinition, fake_reconstruct_port
from app.models import WorkflowCheckpoint, WorkflowRun, WorkflowType
from app.services.chapter_production_graph_reconstruction import (
    reconstruct_scheduler_input,
)

pytestmark = pytest.mark.integration


def _runtime_pin(*, graph_version: str = GRAPH_VERSION) -> dict[str, str]:
    return {
        "scheduler_kind": "langgraph",
        "graph_id": GRAPH_ID,
        "graph_version": graph_version,
    }


async def _insert_run_and_checkpoint(
    session: object,
    *,
    run_id: UUID,
    status: str = "DRAFTING",
    awaiting_user: bool = False,
    graph_version: str = GRAPH_VERSION,
    checkpoint_index: int = 0,
    state_json: dict[str, object] | None = None,
) -> None:
    session.add(  # type: ignore[attr-defined]
        WorkflowRun(
            id=run_id,
            workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
            status=status,
            awaiting_user=awaiting_user,
            metadata_={"chapter_production_runtime": _runtime_pin(graph_version=graph_version)},
        )
    )
    session.add(  # type: ignore[attr-defined]
        WorkflowCheckpoint(
            id=uuid4(),
            workflow_run_id=run_id,
            checkpoint_index=checkpoint_index,
            node_name="reconstruct",
            state_json=state_json if state_json is not None else {},
        )
    )
    await session.commit()  # type: ignore[attr-defined]


async def test_reconstructs_scheduler_input_from_postgresql(
    async_session: object,
) -> None:
    run_id = uuid4()
    attempt_id = uuid4()
    action_request_id = uuid4()
    await _insert_run_and_checkpoint(
        async_session,
        run_id=run_id,
        state_json={
            "attempt_id": str(attempt_id),
            "claim_id": None,
            "action_request_id": str(action_request_id),
        },
    )

    state = await reconstruct_scheduler_input(async_session, run_id)

    assert state["workflow_run_id"] == run_id
    assert state["graph_id"] == GRAPH_ID
    assert state["graph_version"] == GRAPH_VERSION
    assert state["cursor"] == "draft"
    assert state["workflow_checkpoint_index"] == 0
    assert state["attempt_id"] == attempt_id
    assert state["claim_id"] is None
    assert state["action_request_id"] == action_request_id
    assert state["resume_reason"] == "new"


async def test_reconstruction_is_read_only_and_stable_after_restart(
    async_session: object,
) -> None:
    run_id = uuid4()
    await _insert_run_and_checkpoint(async_session, run_id=run_id, checkpoint_index=7)

    first = await reconstruct_scheduler_input(async_session, run_id)
    second = await reconstruct_scheduler_input(async_session, run_id)

    for key in (
        "workflow_run_id",
        "graph_id",
        "graph_version",
        "cursor",
        "workflow_checkpoint_index",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "resume_reason",
    ):
        assert first[key] == second[key]
    assert first["invocation_id"] != second["invocation_id"]


async def test_reconstruction_rejects_unknown_graph_version(
    async_session: object,
) -> None:
    run_id = uuid4()
    await _insert_run_and_checkpoint(async_session, run_id=run_id, graph_version="1")

    with pytest.raises(GraphError):
        await reconstruct_scheduler_input(async_session, run_id)


async def test_reconstruction_rejects_corrupt_checkpoint(
    async_session: object,
) -> None:
    run_id = uuid4()
    await _insert_run_and_checkpoint(
        async_session,
        run_id=run_id,
        state_json={"attempt_id": "not-a-uuid"},
    )

    with pytest.raises(GraphError):
        await reconstruct_scheduler_input(async_session, run_id)


async def test_deleting_graph_state_permits_reconstruction_from_postgresql(
    async_session: object,
) -> None:
    run_id = uuid4()
    await _insert_run_and_checkpoint(async_session, run_id=run_id, checkpoint_index=3)

    state = await reconstruct_scheduler_input(async_session, run_id)
    saver = InMemorySaver()
    definition = GraphDefinition(
        graph_id=GRAPH_ID,
        graph_version=GRAPH_VERSION,
        nodes=(("reconstruct", fake_reconstruct_port, lambda outcome: END),),
    )
    compiled = definition.compile(checkpointer=saver)
    await compiled.ainvoke(state, config=build_config(run_id))
    await saver.adelete_thread(str(run_id))

    rebuilt = await reconstruct_scheduler_input(async_session, run_id)

    assert rebuilt["workflow_run_id"] == state["workflow_run_id"]
    assert rebuilt["graph_id"] == state["graph_id"]
    assert rebuilt["graph_version"] == state["graph_version"]
    assert rebuilt["cursor"] == state["cursor"]
    assert rebuilt["workflow_checkpoint_index"] == state["workflow_checkpoint_index"]
    assert rebuilt["attempt_id"] == state["attempt_id"]
    assert rebuilt["claim_id"] == state["claim_id"]
    assert rebuilt["action_request_id"] == state["action_request_id"]
    assert rebuilt["resume_reason"] == state["resume_reason"]
