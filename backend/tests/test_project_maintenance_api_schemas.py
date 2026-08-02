from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas_project_maintenance import (
    ResolveProjectMaintenanceActionRequest,
    StartProjectMaintenanceRequest,
)
from app.main import create_app


def test_maintenance_start_request_normalizes_and_bounds_advisory_scope() -> None:
    request = StartProjectMaintenanceRequest.model_validate(
        {
            "title": "  Revise the world rule  ",
            "change_request": "  Preserve history while revising the rule.  ",
            "scope_hints": ["world", "timeline"],
        }
    )

    assert request.title == "Revise the world rule"
    assert request.change_request == "Preserve history while revising the rule."
    assert request.scope_hints == ["world", "timeline"]


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "change_request": "valid"},
        {"title": "valid", "change_request": ""},
        {"title": "valid", "change_request": "valid", "scope_hints": ["world", "world"]},
        {"title": "valid", "change_request": "valid", "scope_hints": ["C:/private"]},
        {"title": "valid", "change_request": "valid", "document_id": str(uuid4())},
        {"title": "valid", "change_request": "valid", "provider": "private"},
    ],
)
def test_maintenance_start_request_rejects_empty_duplicate_and_authority_fields(
    payload: dict,
) -> None:
    with pytest.raises(ValidationError):
        StartProjectMaintenanceRequest.model_validate(payload)


@pytest.mark.parametrize("decision", ["approve", "revise", "cancel", "accept_warning"])
def test_maintenance_resolution_accepts_only_declared_decisions(decision: str) -> None:
    assert ResolveProjectMaintenanceActionRequest.model_validate(
        {"decision": decision}
    ).decision == decision


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "force_approve"},
        {"decision": "approve", "feedback": "skip the gate"},
        {"decision": 1},
    ],
)
def test_maintenance_resolution_rejects_unknown_decisions_and_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ResolveProjectMaintenanceActionRequest.model_validate(payload)


def test_maintenance_openapi_is_project_scoped_and_allowlisted() -> None:
    specification = create_app().openapi()
    paths = specification["paths"]
    base = "/api/v1/projects/{project_id}/maintenance"
    assert {"get"} <= set(paths[base])
    assert {"post"} <= set(paths[f"{base}/start"])
    assert {"get"} <= set(paths[f"{base}/{{workflow_run_id}}"])
    assert {"post"} <= set(
        paths[f"{base}/{{workflow_run_id}}/actions/{{action_id}}/resolve"]
    )
    for path, method in (
        (base, "get"),
        (f"{base}/start", "post"),
        (f"{base}/{{workflow_run_id}}", "get"),
        (f"{base}/{{workflow_run_id}}/actions/{{action_id}}/resolve", "post"),
    ):
        assert {"404", "409", "422"} <= set(paths[path][method]["responses"])

    schemas = specification["components"]["schemas"]
    start = schemas["StartProjectMaintenanceRequest"]
    resolve = schemas["ResolveProjectMaintenanceActionRequest"]
    run = schemas["ProjectMaintenanceRunResponse"]
    assert start["additionalProperties"] is False
    assert resolve["additionalProperties"] is False
    assert run["additionalProperties"] is False
    assert set(start["properties"]) == {"title", "change_request", "scope_hints"}
    assert set(resolve["properties"]) == {"decision"}
    assert set(run["properties"]) == {
        "id",
        "maintenance_change_id",
        "type",
        "status",
        "current_node",
        "next_node",
        "awaiting_user",
        "title",
        "change_request",
        "created_at",
        "updated_at",
        "completed_at",
        "affected_items",
        "revision_plan",
        "consistency_review",
        "applied_document_version_ids",
        "pending_action",
    }
