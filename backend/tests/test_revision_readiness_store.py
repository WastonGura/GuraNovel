from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _module():
    return importlib.import_module("app.services.revision_readiness_store")


def test_import_boundary_exposes_only_store_owned_contracts() -> None:
    module = _module()

    assert tuple(module.__all__) == (
        "RevisionReadyPair",
        "RevisionReadinessStore",
        "ready_event_payload",
        "ready_semantic_key",
    )


def test_ready_semantic_key_is_exact_canonical_and_strict() -> None:
    module = _module()

    run_id = uuid4()
    version_id = uuid4()
    assert module.ready_semantic_key(
        workflow_run_id=run_id,
        document_version_id=version_id,
        review_policy_version="chapter-quality-v1",
    ) == (str(run_id), str(version_id), "chapter-quality-v1")

    for invalid_policy in ("", "PRIVATE POLICY", "has space"):
        with pytest.raises(module.ChapterProductionV2ValidationError) as raised:
            module.ready_semantic_key(
                workflow_run_id=run_id,
                document_version_id=version_id,
                review_policy_version=invalid_policy,
            )
        assert raised.value.__cause__ is None


def test_ready_event_payload_is_exact_and_content_free() -> None:
    module = _module()

    checkpoint_id = uuid4()
    payload = module.ready_event_payload(
        chapter_id=uuid4(),
        checkpoint_id=checkpoint_id,
        checkpoint_index=7,
        document_id=uuid4(),
        document_version_id=uuid4(),
        content_hash="a" * 64,
        review_policy_version="chapter-quality-v1",
    )

    assert set(payload) == {
        "chapter_id",
        "checkpoint_id",
        "checkpoint_index",
        "document_id",
        "document_version_id",
        "content_hash",
        "review_policy_version",
        "status",
    }
    assert payload["checkpoint_id"] == str(checkpoint_id)
    assert payload["checkpoint_index"] == 7
    assert payload["status"] == "REVISION_READY"
    assert "content" not in payload
    assert "summary" not in payload
    assert "message" not in payload


def test_ready_pair_is_frozen_and_repr_is_content_free() -> None:
    module = _module()

    pair = module.RevisionReadyPair(
        state=SimpleNamespace(status="REVISION_READY"),
        checkpoint=SimpleNamespace(id=uuid4()),
        event=SimpleNamespace(payload={"status": "REVISION_READY"}),
    )

    assert repr(pair) == "RevisionReadyPair()"
    with pytest.raises((AttributeError, TypeError)):
        pair.state = SimpleNamespace(status="ARCHIVE_UPDATE")


def test_review_slots_treat_empty_report_ids_as_missing_like_old_facade() -> None:
    module = _module()

    state = SimpleNamespace(
        editor_report_id="",
        chief_editor_report_id=None,
        lore_report_id=None,
    )
    assert module._review_slots(state) == ()

    state = SimpleNamespace(
        editor_report_id=None,
        chief_editor_report_id="",
        lore_report_id=None,
    )
    assert module._review_slots(state) == ()


def test_ready_reuse_requires_same_run_and_immediate_successor() -> None:
    module = _module()

    run_id = uuid4()
    run = SimpleNamespace(id=run_id)
    checkpoint = SimpleNamespace(checkpoint_index=5)
    same = SimpleNamespace(workflow_run_id=run_id, checkpoint_index=6)
    wrong_run = SimpleNamespace(workflow_run_id=uuid4(), checkpoint_index=6)
    wrong_index = SimpleNamespace(workflow_run_id=run_id, checkpoint_index=7)

    assert module._is_immediate_successor(
        run=run, checkpoint=checkpoint, pair_checkpoint=same
    )
    assert not module._is_immediate_successor(
        run=run, checkpoint=checkpoint, pair_checkpoint=wrong_run
    )
    assert not module._is_immediate_successor(
        run=run, checkpoint=checkpoint, pair_checkpoint=wrong_index
    )


def test_store_constructor_rejects_non_service() -> None:
    module = _module()

    with pytest.raises(module.ChapterProductionV2ValidationError) as raised:
        module.RevisionReadinessStore(object())
    assert raised.value.__cause__ is None


def test_store_methods_are_scope_bound_async_operations() -> None:
    import inspect

    module = _module()

    assert tuple(inspect.signature(module.RevisionReadinessStore).parameters) == ("service",)
    for method in (
        "enter",
        "validated_pairs",
        "restore_marker",
        "validate_existing_pair",
        "live_review_bindings_locked",
    ):
        member = getattr(module.RevisionReadinessStore, method)
        assert inspect.iscoroutinefunction(member), method
        parameters = inspect.signature(member).parameters
        assert "self" in parameters, method
