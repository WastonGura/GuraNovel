from __future__ import annotations

import asyncio
import inspect
from uuid import UUID, uuid4

import pytest

from app.documents.chapter_segments import derive_chapter_segment_map
from app.services.chapter_production_v2_service import (
    ChapterProductionV2Service,
    ChapterProductionV2ValidationError,
    _new_attempt_id,
    _review_report_slots,
    _safe_cancelled_error,
    _validated_prospective_map,
    compose_initial_markdown,
    merge_segment_replacements,
)
from app.services.document_service import DocumentService, DocumentVersionMetadataError


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
VERSION_ID = UUID("44444444-4444-4444-8444-444444444444")


def test_initial_markdown_composition_is_deterministic_and_bounded() -> None:
    result = compose_initial_markdown(("# Arrival\n\nFirst scene.", "## Warning\n\nSecond scene."))
    assert result == "# Arrival\n\nFirst scene.\n\n## Warning\n\nSecond scene.\n"

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        compose_initial_markdown(("unsafe\x00content",))
    assert str(raised.value) == "Chapter production input is invalid."


def test_revision_merge_replaces_only_exact_source_ranges() -> None:
    source = "# Arrival\n\nOld café scene.\n\n<!-- keep -->\n\n## Warning\n\nOld warning.\n"
    segment_map = derive_chapter_segment_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        content=source,
    )
    target = next(item for item in segment_map.segments if item.content == "Old café scene.")
    merged = merge_segment_replacements(
        source,
        segment_map,
        {target.segment_id: "New 東京 scene."},
    )
    assert merged == source.replace("Old café scene.", "New 東京 scene.")
    assert "<!-- keep -->" in merged
    assert "Old warning." in merged


def test_revision_merge_rejects_unknown_or_empty_replacements_without_leaking_content() -> None:
    source = "# Secret\n\ncanary-private-prose\n"
    segment_map = derive_chapter_segment_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        content=source,
    )
    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        merge_segment_replacements(source, segment_map, {UUID(int=1): "replacement"})
    assert str(raised.value) == "Chapter production input is invalid."
    assert "canary" not in repr(raised.value)


def test_document_version_metadata_accepts_only_content_free_operation_facts() -> None:
    operation_key = "a" * 64
    assert DocumentService._validated_version_metadata(
        {
            "contract_version": "chapter-production-v2",
            "operation_key": operation_key,
        }
    ) == {
        "contract_version": "chapter-production-v2",
        "operation_key": operation_key,
    }
    attempt_id = "88888888-8888-4888-8888-888888888888"
    assert (
        DocumentService._validated_version_metadata(
            {
                "contract_version": "chapter-production-v2",
                "operation_key": operation_key,
                "attempt_id": attempt_id,
            }
        )["attempt_id"]
        == attempt_id
    )
    with pytest.raises(DocumentVersionMetadataError) as raised:
        DocumentService._validated_version_metadata({"provider_output": "canary-private-prose"})
    assert "canary-private-prose" not in repr(raised.value)


def test_prospective_map_validates_the_complete_17_kib_candidate() -> None:
    content = "# Large scene\n\n" + ("x" * (17 * 1024)) + "\n"

    segment_map = _validated_prospective_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        content=content,
    )

    assert segment_map.byte_size == len(content.encode("utf-8"))
    assert "".join(
        item.content for item in segment_map.segments if item.kind.value == "paragraph"
    ) == ("x" * (17 * 1024))
    assert len(segment_map.canonical_bytes()) > 0


def test_every_public_v2_entry_requires_an_actor() -> None:
    for name in (
        "start_from_approved_outline",
        "resume_drafting",
        "resolve_author_action",
        "request_user_feedback_revision",
        "submit_manual_edit",
        "execute_review_revision",
        "load_state",
        "reconcile_indeterminate",
        "acknowledge_provider_no_write",
    ):
        assert (
            "actor_user_id"
            in inspect.signature(getattr(ChapterProductionV2Service, name)).parameters
        )


def test_phase3_author_action_api_is_additive() -> None:
    assert callable(ChapterProductionV2Service.resolve_author_action)
    assert callable(ChapterProductionV2Service.request_user_feedback_revision)
    assert callable(ChapterProductionV2Service.submit_manual_edit)


def test_review_report_slots_bind_each_state_field_to_exact_authority() -> None:
    slots = _review_report_slots(
        editor_report_id=UUID("55555555-5555-4555-8555-555555555555"),
        chief_editor_report_id=UUID("66666666-6666-4666-8666-666666666666"),
        lore_report_id=UUID("77777777-7777-4777-8777-777777777777"),
    )

    assert tuple((mode, role) for _, mode, role in slots) == (
        ("chapter_editor", "editor_agent"),
        ("chapter_chief_final", "chief_editor_agent"),
        ("chapter_final_lore", "lore_agent"),
    )


def test_feedback_work_identity_binds_targets_and_feedback_hash() -> None:
    action_id = UUID("55555555-5555-4555-8555-555555555555")
    first_target = UUID("66666666-6666-4666-8666-666666666666")
    second_target = UUID("77777777-7777-4777-8777-777777777777")
    base = ChapterProductionV2Service._decision_operation_key(
        PROJECT_ID,
        action_id,
        VERSION_ID,
        "feedback",
        target_segment_ids=(first_target,),
        feedback_hash="a" * 64,
    )

    assert base != ChapterProductionV2Service._decision_operation_key(
        PROJECT_ID,
        action_id,
        VERSION_ID,
        "feedback",
        target_segment_ids=(second_target,),
        feedback_hash="a" * 64,
    )
    assert base != ChapterProductionV2Service._decision_operation_key(
        PROJECT_ID,
        action_id,
        VERSION_ID,
        "feedback",
        target_segment_ids=(first_target,),
        feedback_hash="b" * 64,
    )


def test_provider_cancellation_is_rebuilt_without_untrusted_details() -> None:
    unsafe = asyncio.CancelledError("canary-private-provider-cancellation")

    safe = _safe_cancelled_error(unsafe)

    assert type(safe) is asyncio.CancelledError
    assert safe.args == ()
    assert safe.__cause__ is None and safe.__context__ is None
    assert "canary" not in repr(safe)


def test_provider_attempt_ids_are_unique_canonical_content_free_uuids() -> None:
    first = _new_attempt_id()
    second = _new_attempt_id()

    assert first != second
    assert str(UUID(first)) == first
    assert str(UUID(second)) == second


def test_phase3b_resume_and_review_api_is_additive() -> None:
    assert callable(ChapterProductionV2Service.execute_review_revision)
    assert callable(ChapterProductionV2Service.reconcile_indeterminate)
    assert callable(ChapterProductionV2Service.acknowledge_provider_no_write)
    assert callable(ChapterProductionV2Service.load_state)


def test_provider_attempt_metadata_rejects_unbounded_or_malformed_identity() -> None:
    valid = ChapterProductionV2Service._attempt_payload(
        attempt_id="99999999-9999-4999-8999-999999999999",
        key="a" * 64,
        kind="feedback",
        checkpoint_index=1,
        source_document_id=UUID("55555555-5555-4555-8555-555555555555"),
        source_version_id=UUID("66666666-6666-4666-8666-666666666666"),
        action_request_id=UUID("77777777-7777-4777-8777-777777777777"),
        target_segment_ids=(UUID("88888888-8888-4888-8888-888888888888"),),
        feedback_hash="b" * 64,
    )
    assert ChapterProductionV2Service._attempt_metadata_is_valid(valid)

    malformed = dict(valid)
    malformed["source_version_id"] = "not-a-uuid"
    assert not ChapterProductionV2Service._attempt_metadata_is_valid(malformed)
    oversized = dict(valid)
    oversized["target_segment_ids"] = [str(uuid4()) for _ in range(65)]
    assert not ChapterProductionV2Service._attempt_metadata_is_valid(oversized)
    wrong_shape = dict(valid)
    wrong_shape["unexpected"] = "secret"
    assert not ChapterProductionV2Service._attempt_metadata_is_valid(wrong_shape)
