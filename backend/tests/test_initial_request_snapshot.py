from __future__ import annotations

import traceback
import warnings
from uuid import UUID, uuid4

import pytest

from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
)
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.services.initial_request_snapshot import validate_initial_request_snapshot


def _request() -> InitialDraftRequest:
    project_id, chapter_id = uuid4(), uuid4()
    return InitialDraftRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=uuid4(),
        approved_outline=ApprovedOutlineReference(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=uuid4(),
            version_id=uuid4(),
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=uuid4(), index=1, title="Gate", brief="SECRET-OUTLINE"
            ),
        ),
    )


def test_valid_request_returns_fresh_frozen_content_safe_value() -> None:
    source = _request()
    result = validate_initial_request_snapshot(source)
    assert type(result) is InitialDraftRequest and result == source and result is not source
    assert result.approved_outline is not source.approved_outline
    assert result.allowed_segments[0] is not source.allowed_segments[0]
    assert "SECRET-OUTLINE" not in repr(result)


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("request", "project_id", "00000000-0000-0000-0000-000000000001"),
        ("request", "chapter_id", UUID(int=0)),
        ("request", "workflow_run_id", "not-a-uuid"),
        ("outline", "project_id", "00000000-0000-0000-0000-000000000001"),
        ("outline", "document_id", UUID(int=0)),
        ("segment", "segment_id", "00000000-0000-0000-0000-000000000001"),
        ("segment", "index", True),
        ("segment", "title", 1),
        ("segment", "brief", b"secret"),
    ),
)
def test_mutated_source_fields_fail_closed(target: str, field: str, value: object) -> None:
    source = _request()
    selected = {
        "request": source,
        "outline": source.approved_outline,
        "segment": source.allowed_segments[0],
    }[target]
    object.__setattr__(selected, field, value)
    with pytest.raises(ChapterProductionV2ValidationError):
        validate_initial_request_snapshot(source)


@pytest.mark.parametrize("shape", ("strings", "dicts", "list"))
def test_model_construct_shapes_are_not_laundered(shape: str) -> None:
    source = _request()
    if shape == "strings":
        forged = InitialDraftRequest.model_construct(
            project_id=str(source.project_id), chapter_id=str(source.chapter_id),
            workflow_run_id=str(source.workflow_run_id),
            approved_outline=source.approved_outline,
            allowed_segments=source.allowed_segments,
        )
    elif shape == "dicts":
        forged = InitialDraftRequest.model_construct(
            project_id=source.project_id, chapter_id=source.chapter_id,
            workflow_run_id=source.workflow_run_id,
            approved_outline=source.approved_outline.model_dump(mode="json"),
            allowed_segments=source.allowed_segments,
        )
    else:
        forged = InitialDraftRequest.model_construct(
            project_id=source.project_id, chapter_id=source.chapter_id,
            workflow_run_id=source.workflow_run_id,
            approved_outline=source.approved_outline,
            allowed_segments=list(source.allowed_segments),
        )
    with pytest.raises(ChapterProductionV2ValidationError):
        validate_initial_request_snapshot(forged)


def test_hostile_value_emits_no_warning_or_content() -> None:
    class Hostile:
        calls = 0

        def __repr__(self) -> str:
            type(self).calls += 1
            return "SECRET-CANARY"

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            raise RuntimeError("PRIVATE")

        def __hash__(self) -> int:
            type(self).calls += 1
            raise RuntimeError("PRIVATE")

    source = _request()
    object.__setattr__(source, "project_id", Hostile())
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(ChapterProductionV2ValidationError) as raised:
            validate_initial_request_snapshot(source)
    evidence = "".join(
        [str(item.message) for item in captured]
        + [str(raised.value), "".join(traceback.format_exception(raised.value))]
    )
    assert captured == [] and Hostile.calls == 0 and "SECRET-CANARY" not in evidence
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def test_instance_serializer_is_never_called() -> None:
    class HostileSerializer:
        calls = 0

        def to_python(self, *args: object, **kwargs: object) -> object:
            type(self).calls += 1
            warnings.warn("SECRET-SERIALIZER")
            return {}

    source = _request()
    object.__setattr__(source, "__pydantic_serializer__", HostileSerializer())
    with warnings.catch_warnings(record=True) as captured:
        result = validate_initial_request_snapshot(source)
    assert result == source and result is not source
    assert captured == [] and HostileSerializer.calls == 0


def test_result_has_no_uuid_aliases_to_source() -> None:
    source = _request()
    original = source.project_id.int
    result = validate_initial_request_snapshot(source)
    assert result.project_id is not source.project_id
    assert result.approved_outline.document_id is not source.approved_outline.document_id
    assert result.allowed_segments[0].segment_id is not source.allowed_segments[0].segment_id
    object.__setattr__(source.project_id, "int", original + 1)
    assert result.project_id.int == original


def test_forged_exact_uuid_int_does_not_call_hostile_equality() -> None:
    class Hostile:
        calls = 0

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            raise RuntimeError("PRIVATE")

    forged = object.__new__(UUID)
    object.__setattr__(forged, "int", Hostile())
    source = _request()
    object.__setattr__(source, "project_id", forged)
    with pytest.raises(ChapterProductionV2ValidationError):
        validate_initial_request_snapshot(source)
    assert Hostile.calls == 0
