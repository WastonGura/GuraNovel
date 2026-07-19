from __future__ import annotations

from uuid import UUID

import pytest

from app.core.errors import WorkflowStateError
from app.services.project_creation_service import ProjectCreationService
from app.workflows.project_creation import ProjectCreationState, ProjectCreationStatus


def test_transition_rules_fail_closed_for_waiting_and_terminal_states() -> None:
    waiting = ProjectCreationState(
        ProjectCreationStatus.CONCEPT_OPTIONS,
        "concept_review",
        True,
        "b5beae0b-8be1-46b2-bf48-b6cda3239ea7",
    )
    terminal = ProjectCreationState(ProjectCreationStatus.REJECTED, "concept_review", False)

    with pytest.raises(WorkflowStateError, match="awaiting a user decision"):
        ProjectCreationService._require_transition(waiting, ProjectCreationStatus.COMPLETED)
    with pytest.raises(WorkflowStateError, match="terminal"):
        ProjectCreationService._require_transition(terminal, ProjectCreationStatus.CONCEPT_OPTIONS)


def test_safe_event_payload_only_emits_allowlisted_identifiers_and_status() -> None:
    action_id = UUID("b5beae0b-8be1-46b2-bf48-b6cda3239ea7")

    assert ProjectCreationService._safe_event_payload(ProjectCreationStatus.CONCEPT_OPTIONS) == {
        "status": "concept_options"
    }
    assert ProjectCreationService._safe_event_payload(
        ProjectCreationStatus.CONCEPT_REVIEWED, action_id
    ) == {"status": "concept_reviewed", "action_request_id": str(action_id)}
