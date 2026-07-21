import pytest
from pydantic import ValidationError

from app.api.schemas_project_creation import (
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
