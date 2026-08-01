from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import WorkflowStateError
from app.services.maintenance_change_service import (
    MaintenanceAffectedItemCreate,
    MaintenanceChangeService,
    MaintenanceChangeValidationError,
)
from app.workflows.project_maintenance import AffectedItemType, ImpactLevel
from app.workflows.project_maintenance import ProjectMaintenanceStatus


def test_metadata_is_a_bounded_deep_copied_json_object() -> None:
    source = {"outer": {"labels": ["one"]}}

    normalized = MaintenanceChangeService.normalize_metadata(source)
    source["outer"]["labels"].append("two")

    assert normalized == {"outer": {"labels": ["one"]}}
    assert normalized is not source


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"value": float("nan")},
        {"level1": {"level2": {"level3": {"level4": {"level5": {"level6": {"level7": {"level8": {"level9": "too deep"}}}}}}}}},
        {"too_large": "x" * 16_384},
    ],
)
def test_metadata_rejects_non_object_non_json_deep_or_oversized_values(metadata: object) -> None:
    with pytest.raises(MaintenanceChangeValidationError):
        MaintenanceChangeService.normalize_metadata(metadata)


def test_extreme_metadata_nesting_and_cycles_are_safe_domain_errors() -> None:
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(2_000):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    cycle: dict[str, object] = {}
    cycle["self"] = cycle

    for metadata in (nested, cycle):
        with pytest.raises(MaintenanceChangeValidationError):
            MaintenanceChangeService.normalize_metadata(metadata)


def test_affected_item_input_requires_typed_enums_and_non_blank_content() -> None:
    valid = MaintenanceAffectedItemCreate(
        item_type=AffectedItemType.CHAPTER,
        stable_reference="chapter/12",
        impact_level=ImpactLevel.HIGH,
        reason="Continuity changes.",
        existing_chapter_id=uuid4(),
    )
    assert valid.stable_reference == "chapter/12"

    with pytest.raises(MaintenanceChangeValidationError):
        MaintenanceAffectedItemCreate(  # type: ignore[arg-type]
            item_type="chapter",
            stable_reference="chapter/12",
            impact_level=ImpactLevel.HIGH,
            reason="Continuity changes.",
        )
    with pytest.raises(MaintenanceChangeValidationError):
        MaintenanceAffectedItemCreate(
            item_type=AffectedItemType.CHAPTER,
            stable_reference=" ",
            impact_level=ImpactLevel.HIGH,
            reason="Continuity changes.",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stable_reference", "chapter/1\x00private"),
        ("stable_reference", "x" * 2_049),
        ("reason", "reason\x00private"),
        ("reason", "x" * 16_385),
    ],
)
def test_affected_item_input_rejects_control_characters_and_oversized_text(
    field: str, value: str
) -> None:
    values = {
        "item_type": AffectedItemType.CHAPTER,
        "stable_reference": "chapter/1",
        "impact_level": ImpactLevel.LOW,
        "reason": "Reason.",
    }
    values[field] = value
    with pytest.raises(MaintenanceChangeValidationError):
        MaintenanceAffectedItemCreate(**values)  # type: ignore[arg-type]


def test_lifecycle_rejects_jumps_reversions_and_impossible_owned_references() -> None:
    from app.services.maintenance_change_service import _validate_lifecycle

    plan_id = uuid4()
    applied_at = datetime.now(UTC)
    invalid = [
        # New rows must be clean change requests.
        (None, ProjectMaintenanceStatus.CHANGE_REQUESTED, None, plan_id, None, None, False),
        # No forward jumps or reversions.
        (
            ProjectMaintenanceStatus.CHANGE_REQUESTED,
            ProjectMaintenanceStatus.REVISION_PLAN,
            None,
            None,
            None,
            None,
            False,
        ),
        (
            ProjectMaintenanceStatus.REVISION_PLAN,
            ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
            None,
            None,
            None,
            None,
            False,
        ),
        # Confirmation requires a plan.
        (
            ProjectMaintenanceStatus.REVISION_PLAN,
            ProjectMaintenanceStatus.USER_CONFIRMATION,
            None,
            None,
            None,
            None,
            False,
        ),
        # Consistency requires an application timestamp.
        (
            ProjectMaintenanceStatus.APPLY_CHANGE,
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            plan_id,
            plan_id,
            None,
            None,
            True,
        ),
        # Corrective revision clears its old plan.
        (
            ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
            ProjectMaintenanceStatus.REVISION_PLAN,
            plan_id,
            plan_id,
            applied_at,
            applied_at,
            True,
        ),
    ]
    for current, target, old_plan, new_plan, old_applied, new_applied, has_items in invalid:
        with pytest.raises((MaintenanceChangeValidationError, WorkflowStateError)):
            _validate_lifecycle(
                current_status=current,
                target_status=target,
                current_revision_plan_document_id=old_plan,
                target_revision_plan_document_id=new_plan,
                current_applied_at=old_applied,
                target_applied_at=new_applied,
                has_affected_items=has_items,
            )


@pytest.mark.anyio
async def test_duplicate_affected_references_fail_before_database_access() -> None:
    class NoDatabaseAccess:
        async def get(self, *_: object) -> object:
            raise AssertionError("database must not be accessed")

    duplicate = MaintenanceAffectedItemCreate(
        item_type=AffectedItemType.WORLD,
        stable_reference="world/rule",
        impact_level=ImpactLevel.HIGH,
        reason="First reason.",
    )
    with pytest.raises(MaintenanceChangeValidationError, match="must be unique"):
        await MaintenanceChangeService(NoDatabaseAccess()).create_change(  # type: ignore[arg-type]
            project_id=uuid4(),
            workflow_run_id=uuid4(),
            title="Retcon",
            original_change_request="Change a rule.",
            affected_items=(
                duplicate,
                MaintenanceAffectedItemCreate(
                    item_type=AffectedItemType.WORLD,
                    stable_reference=" world/rule ",
                    impact_level=ImpactLevel.LOW,
                    reason="Second reason.",
                ),
            ),
        )


@pytest.mark.anyio
async def test_create_rejects_nonempty_affected_items_before_database_access() -> None:
    item = MaintenanceAffectedItemCreate(
        item_type=AffectedItemType.WORLD,
        stable_reference="world/rule",
        impact_level=ImpactLevel.HIGH,
        reason="Reason.",
    )
    with pytest.raises(MaintenanceChangeValidationError, match="clean change request"):
        await MaintenanceChangeService(object()).create_change(  # type: ignore[arg-type]
            project_id=uuid4(),
            workflow_run_id=uuid4(),
            title="Retcon",
            original_change_request="Change a rule.",
            affected_items=(item,),
        )
