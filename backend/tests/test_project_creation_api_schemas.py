from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.schemas_project_creation import (
    ProjectCreationBlockingIssueResponse,
    ProjectCreationConceptOptionResponse,
    ProjectCreationPendingActionResponse,
    ResolveProjectCreationActionRequest,
    StartProjectCreationRequest,
)


def test_project_creation_start_request_normalizes_and_bounds_transient_preferences() -> None:
    request = StartProjectCreationRequest(
        user_seed="  a haunted library  ",
        target_platform="  web  ",
        preferred_genres=[" mystery "],
        disliked_elements=[" gore "],
        style_preference="  quiet literary  ",
    )

    assert request.user_seed == "a haunted library"
    assert request.preferred_genres == ["mystery"]


@pytest.mark.parametrize(
    "payload",
    [
        {"user_seed": "   "},
        {"user_seed": "valid", "preferred_genres": []},
        {"user_seed": "valid", "disliked_elements": [" "]},
        {"user_seed": "valid", "unknown": "field"},
    ],
)
def test_project_creation_start_request_rejects_empty_and_unrecognized_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        StartProjectCreationRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_seed", 1),
        ("user_seed", True),
        ("user_seed", {}),
        ("target_platform", 1),
        ("target_platform", True),
        ("target_platform", {}),
        ("style_preference", 1),
        ("style_preference", True),
        ("style_preference", {}),
        ("preferred_genres", [1]),
        ("preferred_genres", [True]),
        ("preferred_genres", [{}]),
        ("disliked_elements", [1]),
        ("disliked_elements", [True]),
        ("disliked_elements", [{}]),
    ],
)
def test_project_creation_start_request_rejects_non_string_text_values(
    field: str, value: object
) -> None:
    payload: dict[str, object] = {"user_seed": "valid", field: value}
    with pytest.raises(ValidationError):
        StartProjectCreationRequest.model_validate(payload)


@pytest.mark.parametrize("decision", ["revise", "cancel", "force_approved"])
def test_project_creation_resolution_only_accepts_confirmation_decisions(decision: str) -> None:
    with pytest.raises(ValidationError):
        ResolveProjectCreationActionRequest.model_validate({"decision": decision})


def test_project_creation_concept_option_response_is_strict_and_allowlisted() -> None:
    option = ProjectCreationConceptOptionResponse.model_validate(
        {
            "id": "glass-archive",
            "title": "The Glass Archive",
            "logline": "An archivist discovers a city preserved in glass.",
            "premise": "Every recovered memory changes the city that contains it.",
            "genres": ("fantasy", "mystery"),
        }
    )

    assert option.model_dump() == {
        "id": "glass-archive",
        "title": "The Glass Archive",
        "logline": "An archivist discovers a city preserved in glass.",
        "premise": "Every recovered memory changes the city that contains it.",
        "genres": ("fantasy", "mystery"),
    }
    with pytest.raises(ValidationError):
        ProjectCreationConceptOptionResponse.model_validate(
            {
                **option.model_dump(),
                "provider_payload": "must not cross the public boundary",
            }
        )


def test_project_creation_blocking_issue_response_is_strict_and_allowlisted() -> None:
    issue = ProjectCreationBlockingIssueResponse.model_validate(
        {"code": "premise_conflict", "message": "The premise conflicts with the requested tone."}
    )
    pending = ProjectCreationPendingActionResponse.model_validate(
        {
            "id": UUID("00000000-0000-0000-0000-000000000001"),
            "type": "project_creation_concept_regeneration",
            "status": "pending",
            "allowed_decisions": ("regenerate", "feedback"),
            "review_severity": "blocking",
            "blocking_issues": (issue,),
            "concept_options": (),
        }
    )

    assert pending.model_dump()["blocking_issues"] == (
        {"code": "premise_conflict", "message": "The premise conflicts with the requested tone."},
    )
    with pytest.raises(ValidationError):
        ProjectCreationBlockingIssueResponse.model_validate(
            {**issue.model_dump(), "raw_report": "must not cross the public boundary"}
        )