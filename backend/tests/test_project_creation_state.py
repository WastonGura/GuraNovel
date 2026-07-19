from __future__ import annotations

import pytest

from app.workflows.project_creation import (
    ProjectCreationState,
    ProjectCreationStatus,
    ProjectCreationValidationError,
)


def test_checkpoint_state_round_trip_contains_only_safe_workflow_data() -> None:
    state = ProjectCreationState(
        status=ProjectCreationStatus.CONCEPT_OPTIONS,
        current_node="concept_review",
        awaiting_user=True,
        action_request_id="b5beae0b-8be1-46b2-bf48-b6cda3239ea7",
    )

    assert state.to_checkpoint() == {
        "version": 1,
        "status": "concept_options",
        "current_node": "concept_review",
        "awaiting_user": True,
        "action_request_id": "b5beae0b-8be1-46b2-bf48-b6cda3239ea7",
    }
    assert ProjectCreationState.from_checkpoint(state.to_checkpoint()) == state


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1, "status": "unknown", "current_node": "start", "awaiting_user": False},
        {"version": 1, "status": "user_idea", "current_node": "start", "awaiting_user": "no"},
        {
            "version": 1,
            "status": "concept_options",
            "current_node": "concept_review",
            "awaiting_user": True,
            "user_seed": "do not persist this",
        },
    ],
)
def test_checkpoint_state_rejects_malformed_or_unknown_payloads(payload: object) -> None:
    with pytest.raises(ProjectCreationValidationError):
        ProjectCreationState.from_checkpoint(payload)


def test_checkpoint_state_rejects_boolean_version() -> None:
    with pytest.raises(ProjectCreationValidationError):
        ProjectCreationState.from_checkpoint(
            {
                "version": True,
                "status": "user_idea",
                "current_node": "user_idea",
                "awaiting_user": False,
                "action_request_id": None,
            }
        )


def test_checkpoint_state_rejects_untyped_raw_status() -> None:
    with pytest.raises(ProjectCreationValidationError):
        ProjectCreationState(
            status="user_idea",  # type: ignore[arg-type]
            current_node="user_idea",
            awaiting_user=False,
        )


@pytest.mark.parametrize(
    ("status", "current_node", "action_request_id", "awaiting_user"),
    [
        (ProjectCreationStatus.USER_IDEA, "user_idea", None, 0),
        (
            ProjectCreationStatus.CONCEPT_OPTIONS,
            "concept_review",
            "b5beae0b-8be1-46b2-bf48-b6cda3239ea7",
            1,
        ),
    ],
)
def test_checkpoint_state_rejects_integer_waiting_flags(
    status: ProjectCreationStatus,
    current_node: str,
    action_request_id: str | None,
    awaiting_user: int,
) -> None:
    with pytest.raises(ProjectCreationValidationError):
        ProjectCreationState(
            status=status,
            current_node=current_node,
            awaiting_user=awaiting_user,  # type: ignore[arg-type]
            action_request_id=action_request_id,
        )


def test_checkpoint_state_accepts_actual_boolean_waiting_flags() -> None:
    assert ProjectCreationState(
        status=ProjectCreationStatus.USER_IDEA,
        current_node="user_idea",
        awaiting_user=False,
    ).awaiting_user is False
    assert ProjectCreationState(
        status=ProjectCreationStatus.CONCEPT_OPTIONS,
        current_node="concept_review",
        awaiting_user=True,
        action_request_id="b5beae0b-8be1-46b2-bf48-b6cda3239ea7",
    ).awaiting_user is True
