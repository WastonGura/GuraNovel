"""Pure provider-attempt contract tests."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ValidationError,
)
from app.services.chapter_production_v2_service import ChapterProductionV2Service


MODULE = Path(__file__).resolve().parents[1] / "app/services/provider_attempt_contracts.py"
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("22222222-2222-4222-8222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
VERSION_ID = UUID("44444444-4444-4444-8444-444444444444")
TARGET_IDS = (
    UUID("55555555-5555-4555-8555-555555555555"),
    UUID("66666666-6666-4666-8666-666666666666"),
)
REPORT_IDS = (UUID("77777777-7777-4777-8777-777777777777"),)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _module() -> object:
    return importlib.import_module("app.services.provider_attempt_contracts")


def _fixed(error: ChapterProductionV2ValidationError) -> None:
    assert type(error) is ChapterProductionV2ValidationError
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.details is None


def _initial(module: object) -> object:
    return module.ProviderAttempt.initial(
        attempt_id=uuid4(), operation_key=HASH_A, checkpoint_index=0
    )


def _feedback(module: object) -> object:
    return module.ProviderAttempt.feedback(
        attempt_id=uuid4(),
        operation_key=HASH_A,
        checkpoint_index=2,
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        action_request_id=ACTION_ID,
        target_segment_ids=TARGET_IDS,
        feedback_hash=HASH_B,
    )


def _review(module: object) -> object:
    return module.ProviderAttempt.corrective_revision(
        attempt_id=uuid4(),
        operation_key=HASH_A,
        checkpoint_index=4,
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        target_segment_ids=TARGET_IDS,
        report_ids=REPORT_IDS,
        report_input_hash=HASH_B,
    )


@pytest.mark.parametrize("factory", [_initial, _feedback, _review])
def test_public_construction_failures_are_fixed_and_content_free(factory: object) -> None:
    module = _module()
    factory(module)
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        module.ProviderAttempt(
            attempt_id=UUID(int=0),
            operation_key="PRIVATE",
            kind=module.ProviderAttemptKind.INITIAL,
            checkpoint_index=-1,
        )
    _fixed(raised.value)


@pytest.mark.parametrize("factory", [_initial, _feedback, _review])
def test_attempt_round_trip_is_exact_frozen_and_repr_safe(factory: object) -> None:
    module = _module()
    attempt = factory(module)
    payload = attempt.to_payload()
    assert set(payload) == {
        "attempt_id",
        "key",
        "kind",
        "checkpoint_index",
        "source_document_id",
        "source_version_id",
        "action_request_id",
        "target_segment_ids",
        "feedback_hash",
        "report_ids",
        "report_input_hash",
        "status",
    }
    assert module.ProviderAttempt.from_payload(payload) == attempt
    assert repr(attempt) == "ProviderAttempt()"
    assert type(attempt.target_segment_ids) is tuple
    assert type(attempt.report_ids) is tuple
    with pytest.raises((AttributeError, TypeError)):
        attempt.status = module.ProviderAttemptStatus.FAILED


@pytest.mark.parametrize("factory", [_initial, _feedback, _review])
def test_fresh_token_is_the_only_aba_generation_identity(factory: object) -> None:
    module = _module()
    first = factory(module)
    second = factory(module)
    assert first.attempt_id != second.attempt_id
    assert "generation" not in first.to_payload()
    assert module.same_generation(first, first.attempt_id, first.operation_key)
    assert not module.same_generation(first, second.attempt_id, first.operation_key)
    assert not module.same_generation(first, first.attempt_id, HASH_B)


def test_contract_generates_fresh_exact_nonzero_uuid_tokens() -> None:
    module = _module()
    first, second = module.new_attempt_id(), module.new_attempt_id()
    assert type(first) is UUID
    assert type(second) is UUID
    assert first.int != 0 and second.int != 0 and first != second


@pytest.mark.parametrize("behavior", ["true", "raise"])
def test_same_generation_rejects_hostile_operation_key_without_comparison(
    behavior: str,
) -> None:
    module = _module()
    attempt = _initial(module)
    calls = 0

    class HostileKey:
        def __eq__(self, _: object) -> bool:
            nonlocal calls
            calls += 1
            if behavior == "raise":
                raise RuntimeError("PRIVATE equality")
            return True

    assert not module.same_generation(attempt, attempt.attempt_id, HostileKey())
    assert calls == 0


@pytest.mark.parametrize("factory", [_initial, _feedback, _review])
@pytest.mark.parametrize("status", ["claimed", "failed"])
def test_only_exact_status_transition_is_allowed(factory: object, status: str) -> None:
    module = _module()
    attempt = factory(module)
    transitioned = attempt.with_status(module.ProviderAttemptStatus(status))
    assert transitioned.status.value == status
    assert transitioned.attempt_id == attempt.attempt_id
    assert transitioned.operation_key == attempt.operation_key


def test_failed_attempt_cannot_reuse_the_same_token_as_a_new_claim() -> None:
    module = _module()
    failed = _initial(module).with_status(module.ProviderAttemptStatus.FAILED)
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        failed.with_status(module.ProviderAttemptStatus.CLAIMED)
    _fixed(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", str(UUID(int=0))),
        ("attempt_id", str(uuid4()).upper()),
        ("key", "A" * 64),
        ("key", "a" * 63),
        ("checkpoint_index", -1),
        ("checkpoint_index", True),
        ("status", "completed"),
        ("kind", "corrective_revision"),
        ("extra", "PRIVATE prose"),
    ],
)
def test_payload_rejects_malformed_extra_and_legacy_incompatible_fields(
    field: str, value: object
) -> None:
    module = _module()
    payload = _feedback(module).to_payload()
    payload[field] = value
    assert module.ProviderAttempt.from_payload(payload) is None


@pytest.mark.parametrize(
    "field",
    ["source_document_id", "source_version_id", "action_request_id"],
)
def test_initial_payload_does_not_collapse_malformed_optional_uuid_to_none(field: str) -> None:
    module = _module()
    payload = _initial(module).to_payload()
    payload[field] = "not-a-uuid"
    assert module.ProviderAttempt.from_payload(payload) is None


@pytest.mark.parametrize("field", ["kind", "status"])
def test_payload_rejects_hostile_enum_value_without_hash_or_equality(field: str) -> None:
    module = _module()
    payload = _initial(module).to_payload()
    calls = 0

    class HostileValue:
        def __hash__(self) -> int:
            nonlocal calls
            calls += 1
            raise RuntimeError("PRIVATE hash")

        def __eq__(self, _: object) -> bool:
            nonlocal calls
            calls += 1
            raise RuntimeError("PRIVATE equality")

    payload[field] = HostileValue()
    assert module.ProviderAttempt.from_payload(payload) is None
    assert calls == 0


def test_payload_rejects_non_string_key_without_hashing_it() -> None:
    module = _module()
    payload = _initial(module).to_payload()
    calls = 0

    class HostileKey:
        def __hash__(self) -> int:
            nonlocal calls
            calls += 1
            return hash("kind")

    hostile_key = HostileKey()
    payload[hostile_key] = payload.pop("kind")
    calls = 0

    assert module.ProviderAttempt.from_payload(payload) is None
    assert calls == 0


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        ("initial", {"source_document_id": str(DOCUMENT_ID)}),
        ("initial", {"target_segment_ids": [str(TARGET_IDS[0])]}),
        ("feedback", {"action_request_id": None}),
        ("feedback", {"feedback_hash": None}),
        ("feedback", {"report_ids": [str(REPORT_IDS[0])]}),
        ("review", {"action_request_id": str(ACTION_ID)}),
        ("review", {"report_ids": []}),
        ("review", {"report_input_hash": None}),
    ],
)
def test_cross_kind_shapes_fail_closed(kind: str, updates: dict[str, object]) -> None:
    module = _module()
    attempt = {
        "initial": _initial,
        "feedback": _feedback,
        "review": _review,
    }[kind](module)
    payload = {**attempt.to_payload(), **updates}
    assert module.ProviderAttempt.from_payload(payload) is None


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("target_segment_ids", []),
        ("target_segment_ids", [str(TARGET_IDS[0])] * 2),
        ("target_segment_ids", [str(uuid4())] * 65),
        ("report_ids", [str(REPORT_IDS[0])] * 2),
        ("report_ids", [str(uuid4())] * 17),
    ],
)
def test_empty_duplicate_and_oversized_reference_sets_fail_closed(
    field: str, values: list[str]
) -> None:
    module = _module()
    attempt = _review(module) if field == "report_ids" else _feedback(module)
    payload = {**attempt.to_payload(), field: values}
    assert module.ProviderAttempt.from_payload(payload) is None


def test_operation_keys_match_existing_mechanical_contracts() -> None:
    module = _module()
    initial = module.initial_operation_key(
        project_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        chapter_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        outline_document_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        outline_version_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        outline_content_hash=HASH_A,
        segmenter_version="markdown-v1",
    )
    feedback = module.feedback_operation_key(
        workflow_run_id=RUN_ID,
        action_request_id=ACTION_ID,
        source_version_id=VERSION_ID,
        target_segment_ids=TARGET_IDS,
        feedback_hash=HASH_B,
    )
    review = module.corrective_revision_operation_key(
        workflow_run_id=RUN_ID,
        source_version_id=VERSION_ID,
        report_ids=REPORT_IDS,
        target_segment_ids=TARGET_IDS,
        report_input_hash=HASH_B,
    )
    assert (initial, feedback, review) == (
        "8d5e1898dd6d43dfe7cfa3a3390b7b2884d131de18fbeab2ca6d25239c824241",
        "aa63bb48f0ead83ac031a78bf5e5584fee293cfc1bd78bd33ee5c6ea7b070632",
        "8dd2fb61fe01674c3ba76f88b72ae128fd58c6f1dd2552ca95738188f8c7a7c8",
    )
    assert initial == ChapterProductionV2Service._operation_key(
        project_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        chapter_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        outline_document_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        outline_version_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        outline_content_hash=HASH_A,
    )
    assert feedback == ChapterProductionV2Service._decision_operation_key(
        RUN_ID,
        ACTION_ID,
        VERSION_ID,
        "feedback",
        target_segment_ids=TARGET_IDS,
        feedback_hash=HASH_B,
    )
    assert review == ChapterProductionV2Service._review_operation_key(
        workflow_run_id=RUN_ID,
        source_version_id=VERSION_ID,
        report_ids=REPORT_IDS,
        target_segment_ids=TARGET_IDS,
        report_input_hash=HASH_B,
    )


@pytest.mark.parametrize("factory", [_initial, _feedback, _review])
def test_payload_acceptance_matches_frozen_service_validator(factory: object) -> None:
    module = _module()
    payload = factory(module).to_payload()
    assert ChapterProductionV2Service._attempt_metadata_is_valid(payload)
    assert module.ProviderAttempt.from_payload(payload) is not None


@pytest.mark.parametrize(
    ("builder", "kwargs"),
    [
        (
            "initial_operation_key",
            {
                "project_id": UUID(int=0),
                "chapter_id": RUN_ID,
                "outline_document_id": DOCUMENT_ID,
                "outline_version_id": VERSION_ID,
                "outline_content_hash": HASH_A,
                "segmenter_version": "markdown-v1",
            },
        ),
        (
            "feedback_operation_key",
            {
                "workflow_run_id": RUN_ID,
                "action_request_id": ACTION_ID,
                "source_version_id": VERSION_ID,
                "target_segment_ids": TARGET_IDS,
                "feedback_hash": "B" * 64,
            },
        ),
        (
            "corrective_revision_operation_key",
            {
                "workflow_run_id": RUN_ID,
                "source_version_id": VERSION_ID,
                "report_ids": (),
                "target_segment_ids": TARGET_IDS,
                "report_input_hash": HASH_B,
            },
        ),
        (
            "initial_operation_key",
            {
                "project_id": RUN_ID,
                "chapter_id": ACTION_ID,
                "outline_document_id": DOCUMENT_ID,
                "outline_version_id": VERSION_ID,
                "outline_content_hash": HASH_A,
                "segmenter_version": "future-v2",
            },
        ),
    ],
)
def test_operation_key_builders_reject_invalid_mechanical_inputs(
    builder: str, kwargs: dict[str, object]
) -> None:
    module = _module()
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        getattr(module, builder)(**kwargs)
    _fixed(raised.value)


def test_contract_module_has_no_authority_and_stays_within_budget() -> None:
    assert MODULE.exists()
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for prefix in (
        "sqlalchemy",
        "app.models",
        "app.agents",
        "app.services.chapter_production_v2_service",
        "app.services.chapter_production_repository",
        "app.services.document_service",
        "app.workspace",
        "langgraph",
        "pathlib",
        "os",
    ):
        assert not any(name == prefix or name.startswith(f"{prefix}.") for name in imports)
    assert len(source.splitlines()) <= 500
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 250
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno and node.end_lineno - node.lineno + 1 <= 70
    assert tuple(inspect.signature(_module().ProviderAttempt.from_payload).parameters) == (
        "payload",
    )
