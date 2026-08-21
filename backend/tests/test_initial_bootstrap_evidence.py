from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)
from app.services.initial_bootstrap_evidence import (
    InitialBootstrapBinding,
    pristine_checkpoint,
    pristine_run_metadata,
    validate_pristine_initial_evidence,
)
from app.services.chapter_production_runtime import (
    chapter_production_langgraph_pin,
    persisted_runtime_pin,
)


MODULE = (
    Path(__file__).resolve().parents[1]
    / "app/services/initial_bootstrap_evidence.py"
)
HASH = "a" * 64
OPERATION_KEY = "b" * 64


def _binding() -> InitialBootstrapBinding:
    return InitialBootstrapBinding(
        workflow_run_id=uuid4(),
        chapter_id=uuid4(),
        outline_document_id=uuid4(),
        outline_version_id=uuid4(),
        outline_content_hash=HASH,
        operation_key=OPERATION_KEY,
        chief_editor_required=True,
    )


def _evidence(binding: InitialBootstrapBinding) -> dict[str, object]:
    return {
        "workflow_type": "chapter_production",
        "status": "DRAFTING",
        "current_node": "drafting",
        "next_node": None,
        "awaiting_user": False,
        "metadata": pristine_run_metadata(binding),
        "checkpoint_markers": ((0, "drafting", pristine_checkpoint(binding)),),
    }


def _validate(binding: InitialBootstrapBinding, **changes: object) -> dict[str, object]:
    evidence = {**_evidence(binding), **changes}
    return validate_pristine_initial_evidence(binding, **evidence)


def _assert_fixed_error(call: object) -> None:
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        call()  # type: ignore[operator]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_builds_exact_pristine_metadata_and_checkpoint() -> None:
    binding = _binding()
    metadata = pristine_run_metadata(binding)
    checkpoint = pristine_checkpoint(binding)
    assert metadata == {
        "contract_version": "chapter-production-v2",
        "review_policy_version": "chapter-quality-v1",
        "chief_editor_required": True,
        "outline_document_id": str(binding.outline_document_id),
        "outline_version_id": str(binding.outline_version_id),
        "outline_content_hash": HASH,
        "segmenter_version": "markdown-v1",
        "operation_key": OPERATION_KEY,
        "provider_attempt": None,
        "reviewer_claim": None,
        "chapter_production_runtime": {
            "scheduler_kind": "service_v2",
            "graph_id": "chapter-production-v2",
            "graph_version": "0",
        },
    }
    assert checkpoint["chapter_workflow_run_id"] == str(binding.workflow_run_id)
    assert checkpoint["chapter_id"] == str(binding.chapter_id)
    assert checkpoint["status"] == "DRAFTING"
    assert checkpoint["current_node"] == "drafting"
    for field in (
        "document_id",
        "document_version_id",
        "content_hash",
        "editor_report_id",
        "chief_editor_report_id",
        "lore_report_id",
        "action_request_id",
        "action_kind",
        "failed_from_status",
        "failure_code",
    ):
        assert checkpoint[field] is None


def test_langgraph_runtime_is_explicit_and_preserved_during_validation() -> None:
    binding = _binding()
    pin = chapter_production_langgraph_pin()
    metadata = pristine_run_metadata(binding)
    metadata["chapter_production_runtime"] = pin

    assert persisted_runtime_pin(metadata) == pin
    assert _validate(binding, metadata=metadata) == metadata
    assert pristine_run_metadata(binding)["chapter_production_runtime"] == {
        "scheduler_kind": "service_v2",
        "graph_id": "chapter-production-v2",
        "graph_version": "0",
    }


def test_validates_current_and_normalizes_only_documented_legacy_metadata() -> None:
    binding = _binding()
    assert _validate(binding) == pristine_run_metadata(binding)
    legacy = pristine_run_metadata(binding)
    del legacy["reviewer_claim"]
    assert _validate(binding, metadata=legacy) == pristine_run_metadata(binding)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("workflow_type", "PROJECT_CREATION"),
        ("status", "FAILED"),
        ("current_node", "author_revision"),
        ("next_node", "drafting"),
        ("awaiting_user", True),
        (
            "checkpoint_markers",
            ((1, "drafting", pristine_checkpoint(_binding())),),
        ),
    ),
)
def test_rejects_non_pristine_run_or_checkpoint_projection(
    field: str, value: object
) -> None:
    binding = _binding()
    _assert_fixed_error(lambda: _validate(binding, **{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("document_id", str(uuid4())),
        ("document_version_id", str(uuid4())),
        ("content_hash", HASH),
        ("editor_report_id", str(uuid4())),
        ("action_request_id", str(uuid4())),
        ("failed_from_status", "DRAFTING"),
    ),
)
def test_rejects_any_non_pristine_checkpoint_binding(
    field: str, value: object
) -> None:
    binding = _binding()
    state = pristine_checkpoint(binding)
    state[field] = value
    _assert_fixed_error(
        lambda: _validate(binding, checkpoint_markers=((0, "drafting", state),))
    )


@pytest.mark.parametrize(
    "markers",
    (
        (),
        (
            (0, "drafting", {}),
            (1, "drafting", {}),
        ),
        [(0, "drafting", {})],
        ((0, "drafting", {}),),
        ((0, "author_revision", {}),),
    ),
)
def test_rejects_missing_duplicate_or_malformed_checkpoint_marker_collection(
    markers: object,
) -> None:
    binding = _binding()
    _assert_fixed_error(lambda: _validate(binding, checkpoint_markers=markers))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("contract_version", "other"),
        ("review_policy_version", "other"),
        ("chief_editor_required", False),
        ("outline_document_id", str(uuid4())),
        ("outline_version_id", str(uuid4())),
        ("outline_content_hash", "c" * 64),
        ("segmenter_version", "other"),
        ("operation_key", "d" * 64),
        ("provider_attempt", {}),
        ("reviewer_claim", {}),
    ),
)
def test_rejects_metadata_drift(field: str, value: object) -> None:
    binding = _binding()
    metadata = pristine_run_metadata(binding)
    metadata[field] = value
    _assert_fixed_error(lambda: _validate(binding, metadata=metadata))


def test_rejects_extra_missing_and_hostile_payload_shapes_without_dispatch() -> None:
    binding = _binding()
    extra = {**pristine_run_metadata(binding), "extra": None}
    missing = pristine_run_metadata(binding)
    del missing["operation_key"]

    class HostileStr(str):
        def __hash__(self) -> int:
            return str.__hash__(self)

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("PRIVATE equality")

    hostile = dict(pristine_run_metadata(binding))
    hostile[HostileStr("operation_key")] = hostile.pop("operation_key")
    for metadata in (extra, missing, hostile):
        _assert_fixed_error(lambda metadata=metadata: _validate(binding, metadata=metadata))


def test_binding_is_exact_frozen_content_safe_and_rejects_subclasses() -> None:
    binding = _binding()
    assert repr(binding) == "InitialBootstrapBinding()"
    assert not hasattr(binding, "__dict__")
    with pytest.raises(FrozenInstanceError):
        binding.chapter_id = uuid4()  # type: ignore[misc]

    class HostileUUID(UUID):
        def __getattribute__(self, name: str) -> object:
            if name == "int":
                raise RuntimeError("PRIVATE int")
            return super().__getattribute__(name)

    hostile = HostileUUID(str(uuid4()))
    _assert_fixed_error(
        lambda: InitialBootstrapBinding(
            workflow_run_id=hostile,
            chapter_id=uuid4(),
            outline_document_id=uuid4(),
            outline_version_id=uuid4(),
            outline_content_hash=HASH,
            operation_key=OPERATION_KEY,
            chief_editor_required=True,
        )
    )


@pytest.mark.parametrize(
    "entrypoint",
    (
        pristine_run_metadata,
        pristine_checkpoint,
        lambda binding: _validate(binding),
    ),
)
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("operation_key", "PRIVATE"),
        ("chief_editor_required", 1),
        ("outline_content_hash", "PRIVATE"),
    ),
)
def test_every_public_boundary_revalidates_mutated_exact_binding(
    entrypoint: object, field: str, value: object
) -> None:
    binding = _binding()
    object.__setattr__(binding, field, value)
    _assert_fixed_error(lambda: entrypoint(binding))  # type: ignore[operator]


@pytest.mark.parametrize(
    "entrypoint",
    (
        pristine_run_metadata,
        pristine_checkpoint,
        lambda binding: _validate(binding),
    ),
)
def test_every_public_boundary_rejects_incomplete_forged_binding(
    entrypoint: object,
) -> None:
    forged = object.__new__(InitialBootstrapBinding)
    _assert_fixed_error(lambda: entrypoint(forged))  # type: ignore[operator]


def test_module_has_pure_imports_and_small_budget() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = (
        "sqlalchemy",
        "app.models",
        "repository",
        "session",
        "DocumentService",
        "ProviderAttempt",
        "Agent",
        "pathlib",
        "os.",
        "chapter_production_v2_service",
    )
    assert all(item not in source for item in forbidden)
    assert len(source.splitlines()) <= 180
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 160
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 60
