from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from app.models.enums import ActionRequestStatus
from app.workflows.project_maintenance import (
    AffectedItem,
    AffectedItemType,
    ImpactLevel,
    MaintenanceConfirmationKind,
    MaintenanceDecision,
    MaintenanceReviewOutcome,
    ProjectMaintenanceState,
    ProjectMaintenanceStatus,
    ProjectMaintenanceValidationError,
)


ACTION_ID = "b5beae0b-8be1-46b2-bf48-b6cda3239ea7"
AFFECTED_ITEM_ID = "0f605479-dddf-4022-93ab-6e2216908af7"
LORE_REPORT_ID = "0a8a5147-b2b9-4796-9eb3-10530e47b1d8"
CHIEF_REPORT_ID = "28db6a5f-425f-4132-854e-a88ce2572683"
REVISION_PLAN_ID = "d21fbfe7-5fe7-4e9a-806a-1f5de3b61a49"
REVISION_PLAN_VERSION_ID = "f9f5c018-fe34-422f-b361-f24004fbcaa8"
PROPOSED_VERSION_ID = "a90f3a5d-9ded-4294-b15b-78f4c759f3dd"
APPLIED_VERSION_ID = "73e5f184-a997-45a1-a3f9-93a79d1472d1"
CONSISTENCY_REPORT_ID = "91a38213-71c7-4adc-b50c-a3f2bb2ce22b"


NODES = {
    ProjectMaintenanceStatus.CHANGE_REQUESTED: "user_change_request",
    ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS: "lore_impact_analysis",
    ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS: "chief_editor_impact_review",
    ProjectMaintenanceStatus.REVISION_PLAN: "revision_plan",
    ProjectMaintenanceStatus.USER_CONFIRMATION: "user_confirm_revision",
    ProjectMaintenanceStatus.APPLY_CHANGE: "apply_revision",
    ProjectMaintenanceStatus.CONSISTENCY_REVIEW: "consistency_review",
    ProjectMaintenanceStatus.PROJECT_UPDATED: "project_updated",
    ProjectMaintenanceStatus.CANCELLED: "cancelled",
}


def state(
    status: ProjectMaintenanceStatus,
    *,
    gate_outcome: MaintenanceReviewOutcome = MaintenanceReviewOutcome.PASSED,
) -> ProjectMaintenanceState:
    waiting = status is ProjectMaintenanceStatus.USER_CONFIRMATION
    has_plan = status in {
        ProjectMaintenanceStatus.REVISION_PLAN,
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        ProjectMaintenanceStatus.APPLY_CHANGE,
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        ProjectMaintenanceStatus.PROJECT_UPDATED,
        ProjectMaintenanceStatus.CANCELLED,
    }
    has_consistency = status in {
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        ProjectMaintenanceStatus.PROJECT_UPDATED,
    }
    return ProjectMaintenanceState(
        status=status,
        current_node=NODES[status],
        awaiting_user=waiting,
        action_request_id=ACTION_ID if waiting else None,
        confirmation_kind=(
            MaintenanceConfirmationKind.REVISION_CONFIRMATION if waiting else None
        ),
        gate_review_outcome=gate_outcome if waiting else None,
        lore_impact_report_id=(
            None if status is ProjectMaintenanceStatus.CHANGE_REQUESTED else LORE_REPORT_ID
        ),
        chief_impact_report_id=(
            None
            if status
            in {
                ProjectMaintenanceStatus.CHANGE_REQUESTED,
                ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
            }
            else CHIEF_REPORT_ID
        ),
        revision_plan_document_id=REVISION_PLAN_ID if has_plan else None,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID if has_plan else None,
        applied_document_version_ids=(APPLIED_VERSION_ID,) if has_consistency else (),
        consistency_report_id=CONSISTENCY_REPORT_ID if has_consistency else None,
    )


def test_status_values_match_the_project_maintenance_design() -> None:
    assert [status.value for status in ProjectMaintenanceStatus] == [
        "CHANGE_REQUESTED",
        "LORE_IMPACT_ANALYSIS",
        "CHIEF_EDITOR_IMPACT_ANALYSIS",
        "REVISION_PLAN",
        "USER_CONFIRMATION",
        "APPLY_CHANGE",
        "CONSISTENCY_REVIEW",
        "PROJECT_UPDATED",
        "CANCELLED",
    ]


def test_affected_item_is_a_strict_frozen_contract() -> None:
    item = AffectedItem(
        type=AffectedItemType.CHAPTER,
        ref="chapters/chapter_001",
        impact_level=ImpactLevel.HIGH,
        reason="The revised rule changes the scene outcome.",
    )

    assert item.type is AffectedItemType.CHAPTER
    with pytest.raises(FrozenInstanceError):
        item.reason = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "type": "chapter",
            "ref": "chapters/chapter_001",
            "impact_level": ImpactLevel.HIGH,
            "reason": "reason",
        },
        {
            "type": AffectedItemType.CHAPTER,
            "ref": "",
            "impact_level": ImpactLevel.HIGH,
            "reason": "reason",
        },
        {
            "type": AffectedItemType.CHAPTER,
            "ref": "chapters/chapter_001",
            "impact_level": "high",
            "reason": "reason",
        },
        {
            "type": AffectedItemType.CHAPTER,
            "ref": "chapters/chapter_001",
            "impact_level": ImpactLevel.HIGH,
            "reason": "   ",
        },
    ],
)
def test_affected_item_rejects_untyped_or_empty_fields(kwargs: dict[str, object]) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        AffectedItem(**kwargs)  # type: ignore[arg-type]


def test_checkpoint_round_trip_preserves_only_content_free_references() -> None:
    original = ProjectMaintenanceState(
        status=ProjectMaintenanceStatus.USER_CONFIRMATION,
        current_node="user_confirm_revision",
        awaiting_user=True,
        action_request_id=ACTION_ID,
        confirmation_kind=MaintenanceConfirmationKind.REVISION_CONFIRMATION,
        gate_review_outcome=MaintenanceReviewOutcome.WARNING,
        affected_item_ids=(AFFECTED_ITEM_ID,),
        lore_impact_report_id=LORE_REPORT_ID,
        chief_impact_report_id=CHIEF_REPORT_ID,
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
        proposed_document_version_ids=(PROPOSED_VERSION_ID,),
        applied_document_version_ids=(APPLIED_VERSION_ID,),
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )

    checkpoint = original.to_checkpoint()

    assert checkpoint == {
        "version": 1,
        "status": "USER_CONFIRMATION",
        "current_node": "user_confirm_revision",
        "awaiting_user": True,
        "action_request_id": ACTION_ID,
        "confirmation_kind": "revision_confirmation",
        "gate_review_outcome": "warning",
        "affected_item_ids": [AFFECTED_ITEM_ID],
        "lore_impact_report_id": LORE_REPORT_ID,
        "chief_impact_report_id": CHIEF_REPORT_ID,
        "revision_plan_document_id": REVISION_PLAN_ID,
        "revision_plan_version_id": REVISION_PLAN_VERSION_ID,
        "proposed_document_version_ids": [PROPOSED_VERSION_ID],
        "applied_document_version_ids": [APPLIED_VERSION_ID],
        "consistency_report_id": CONSISTENCY_REPORT_ID,
    }
    assert ProjectMaintenanceState.from_checkpoint(checkpoint) == original
    assert not {
        "change_request",
        "affected_items",
        "reason",
        "report",
        "content",
        "prompt",
        "provider_output",
    } & checkpoint.keys()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("status"),
        lambda payload: payload.update(extra="value"),
        lambda payload: payload.update(version=True),
        lambda payload: payload.update(version=2),
        lambda payload: payload.update(status="UNKNOWN"),
        lambda payload: payload.update(awaiting_user=1),
        lambda payload: payload.update(affected_item_ids=(AFFECTED_ITEM_ID,)),
        lambda payload: payload.update(action_request_id=ACTION_ID.upper()),
        lambda payload: payload.update(revision_plan_version_id=REVISION_PLAN_VERSION_ID.upper()),
        lambda payload: payload.update(affected_item_ids=[AFFECTED_ITEM_ID.upper()]),
        lambda payload: payload.update(affected_item_ids=[AFFECTED_ITEM_ID] * 2),
    ],
)
def test_checkpoint_rejects_unknown_shape_types_versions_and_noncanonical_ids(
    mutation: object,
) -> None:
    payload = state(ProjectMaintenanceStatus.USER_CONFIRMATION).to_checkpoint()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState.from_checkpoint(payload)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "00000000-0000-0000-0000-000000000000",
        "not-a-uuid",
        42,
    ],
)
def test_uuid_boundaries_reject_nil_malformed_and_non_string_ids(invalid_id: object) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
            "lore_impact_analysis",
            False,
            affected_item_ids=(invalid_id,),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "field",
    ["affected_item_ids", "proposed_document_version_ids", "applied_document_version_ids"],
)
def test_uuid_collections_reject_duplicates(field: str) -> None:
    kwargs: dict[str, object] = {
        "status": ProjectMaintenanceStatus.REVISION_PLAN,
        "current_node": "revision_plan",
        "awaiting_user": False,
        "lore_impact_report_id": LORE_REPORT_ID,
        "chief_impact_report_id": CHIEF_REPORT_ID,
        "revision_plan_document_id": REVISION_PLAN_ID,
        "revision_plan_version_id": REVISION_PLAN_VERSION_ID,
        field: (PROPOSED_VERSION_ID, PROPOSED_VERSION_ID),
    }
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(**kwargs)  # type: ignore[arg-type]


def test_proposed_and_applied_version_collections_cannot_overlap() -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.REVISION_PLAN,
            "revision_plan",
            False,
            lore_impact_report_id=LORE_REPORT_ID,
            chief_impact_report_id=CHIEF_REPORT_ID,
            revision_plan_document_id=REVISION_PLAN_ID,
            revision_plan_version_id=REVISION_PLAN_VERSION_ID,
            proposed_document_version_ids=(PROPOSED_VERSION_ID,),
            applied_document_version_ids=(PROPOSED_VERSION_ID,),
            consistency_report_id=CONSISTENCY_REPORT_ID,
        )


def test_project_updated_rejects_an_empty_applied_version_collection() -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            "project_updated",
            False,
            lore_impact_report_id=LORE_REPORT_ID,
            chief_impact_report_id=CHIEF_REPORT_ID,
            revision_plan_document_id=REVISION_PLAN_ID,
            revision_plan_version_id=REVISION_PLAN_VERSION_ID,
        )


def test_state_is_frozen_and_rejects_a_node_or_action_mismatch() -> None:
    original = state(ProjectMaintenanceStatus.CHANGE_REQUESTED)
    with pytest.raises(FrozenInstanceError):
        original.awaiting_user = True  # type: ignore[misc]

    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.CHANGE_REQUESTED,
            "wrong_node",
            False,
        )


def test_phase_reference_invariants_reject_future_or_missing_references() -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.CHANGE_REQUESTED,
            "user_change_request",
            False,
            applied_document_version_ids=(APPLIED_VERSION_ID,),
            consistency_report_id=CONSISTENCY_REPORT_ID,
        )
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.PROJECT_UPDATED,
            "project_updated",
            False,
            revision_plan_document_id=REVISION_PLAN_ID,
            revision_plan_version_id=REVISION_PLAN_VERSION_ID,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "status": ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
            "current_node": "lore_impact_analysis",
            "chief_impact_report_id": CHIEF_REPORT_ID,
        },
        {
            "status": ProjectMaintenanceStatus.REVISION_PLAN,
            "current_node": "revision_plan",
            "lore_impact_report_id": LORE_REPORT_ID,
            "chief_impact_report_id": CHIEF_REPORT_ID,
            "proposed_document_version_ids": (PROPOSED_VERSION_ID,),
        },
        {
            "status": ProjectMaintenanceStatus.REVISION_PLAN,
            "current_node": "revision_plan",
            "lore_impact_report_id": LORE_REPORT_ID,
            "chief_impact_report_id": CHIEF_REPORT_ID,
            "consistency_report_id": CONSISTENCY_REPORT_ID,
        },
        {
            "status": ProjectMaintenanceStatus.APPLY_CHANGE,
            "current_node": "apply_revision",
            "lore_impact_report_id": LORE_REPORT_ID,
            "chief_impact_report_id": CHIEF_REPORT_ID,
            "revision_plan_document_id": REVISION_PLAN_ID,
            "revision_plan_version_id": REVISION_PLAN_VERSION_ID,
            "applied_document_version_ids": (APPLIED_VERSION_ID,),
            "consistency_report_id": CONSISTENCY_REPORT_ID,
        },
    ],
)
def test_phase_reference_invariants_reject_forbidden_reference_combinations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(awaiting_user=False, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "extra_refs",
    [
        {"applied_document_version_ids": (APPLIED_VERSION_ID,)},
        {
            "applied_document_version_ids": (APPLIED_VERSION_ID,),
            "consistency_report_id": CONSISTENCY_REPORT_ID,
        },
    ],
)
def test_cancelled_state_rejects_applied_or_consistency_references(
    extra_refs: dict[str, object],
) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.CANCELLED,
            "cancelled",
            False,
            lore_impact_report_id=LORE_REPORT_ID,
            chief_impact_report_id=CHIEF_REPORT_ID,
            revision_plan_document_id=REVISION_PLAN_ID,
            revision_plan_version_id=REVISION_PLAN_VERSION_ID,
            **extra_refs,  # type: ignore[arg-type]
        )


def test_cancelled_checkpoint_rejects_applied_and_consistency_references() -> None:
    checkpoint = state(ProjectMaintenanceStatus.CANCELLED).to_checkpoint()
    checkpoint.update(
        applied_document_version_ids=[APPLIED_VERSION_ID],
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )

    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState.from_checkpoint(checkpoint)


@pytest.mark.parametrize(
    "status",
    [ProjectMaintenanceStatus.REVISION_PLAN, ProjectMaintenanceStatus.USER_CONFIRMATION],
)
def test_corrective_revision_state_requires_the_originating_consistency_report(
    status: ProjectMaintenanceStatus,
) -> None:
    waiting = status is ProjectMaintenanceStatus.USER_CONFIRMATION
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            status,
            NODES[status],
            waiting,
            ACTION_ID if waiting else None,
            MaintenanceConfirmationKind.REVISION_CONFIRMATION if waiting else None,
            MaintenanceReviewOutcome.PASSED if waiting else None,
            lore_impact_report_id=LORE_REPORT_ID,
            chief_impact_report_id=CHIEF_REPORT_ID,
            revision_plan_document_id=REVISION_PLAN_ID,
            revision_plan_version_id=REVISION_PLAN_VERSION_ID,
            applied_document_version_ids=(APPLIED_VERSION_ID,),
        )


def test_corrective_revision_checkpoint_requires_consistency_report() -> None:
    checkpoint = state(ProjectMaintenanceStatus.REVISION_PLAN).to_checkpoint()
    checkpoint["applied_document_version_ids"] = [APPLIED_VERSION_ID]

    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState.from_checkpoint(checkpoint)


def test_executing_nodes_allow_partial_local_outputs_but_not_future_outputs() -> None:
    lore = ProjectMaintenanceState(
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        "lore_impact_analysis",
        False,
        affected_item_ids=(AFFECTED_ITEM_ID,),
        lore_impact_report_id=LORE_REPORT_ID,
    )
    chief = ProjectMaintenanceState(
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        "chief_editor_impact_review",
        False,
        affected_item_ids=(AFFECTED_ITEM_ID,),
        lore_impact_report_id=LORE_REPORT_ID,
        chief_impact_report_id=CHIEF_REPORT_ID,
    )
    applying = ProjectMaintenanceState(
        ProjectMaintenanceStatus.APPLY_CHANGE,
        "apply_revision",
        False,
        lore_impact_report_id=LORE_REPORT_ID,
        chief_impact_report_id=CHIEF_REPORT_ID,
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
        applied_document_version_ids=(APPLIED_VERSION_ID,),
    )
    reviewing = ProjectMaintenanceState(
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        "consistency_review",
        False,
        lore_impact_report_id=LORE_REPORT_ID,
        chief_impact_report_id=CHIEF_REPORT_ID,
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
        applied_document_version_ids=(APPLIED_VERSION_ID,),
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )

    assert lore.lore_impact_report_id == LORE_REPORT_ID
    assert chief.chief_impact_report_id == CHIEF_REPORT_ID
    assert applying.applied_document_version_ids == (APPLIED_VERSION_ID,)
    assert reviewing.consistency_report_id == CONSISTENCY_REPORT_ID
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.USER_CONFIRMATION,
            "user_confirm_revision",
            True,
            None,
        )
    with pytest.raises(ProjectMaintenanceValidationError):
        ProjectMaintenanceState(
            ProjectMaintenanceStatus.REVISION_PLAN,
            "revision_plan",
            False,
            ACTION_ID,
        )


LEGAL_DIRECT_TRANSITIONS = {
    (ProjectMaintenanceStatus.CHANGE_REQUESTED, ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS),
}


@pytest.mark.parametrize(("source", "target"), sorted(LEGAL_DIRECT_TRANSITIONS, key=str))
def test_every_direct_transition_is_explicit(
    source: ProjectMaintenanceStatus, target: ProjectMaintenanceStatus
) -> None:
    action_id = ACTION_ID if target is ProjectMaintenanceStatus.USER_CONFIRMATION else None
    transitioned = state(source).transition_to(target, action_request_id=action_id)

    assert transitioned.status is target
    assert transitioned.current_node == NODES[target]


def test_every_unlisted_direct_transition_fails_closed() -> None:
    for source, target in product(ProjectMaintenanceStatus, repeat=2):
        if (source, target) in LEGAL_DIRECT_TRANSITIONS:
            continue
        with pytest.raises(ProjectMaintenanceValidationError):
            action_id = ACTION_ID if target is ProjectMaintenanceStatus.USER_CONFIRMATION else None
            state(source).transition_to(target, action_request_id=action_id)


def test_phase_specific_methods_cover_plan_apply_and_corrective_loop() -> None:
    lore = state(ProjectMaintenanceStatus.CHANGE_REQUESTED).transition_to(
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS
    )
    chief = lore.record_lore_impact(lore_impact_report_id=LORE_REPORT_ID)
    revision = chief.record_chief_impact(chief_impact_report_id=CHIEF_REPORT_ID)
    planned = revision.record_revision_plan(
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
    )
    waiting = planned.request_revision_confirmation(
        action_request_id=ACTION_ID,
        review_outcome=MaintenanceReviewOutcome.PASSED,
    )
    applying = waiting.resolve_confirmation(
        live_action_request_id=ACTION_ID,
        action_status=ActionRequestStatus.PENDING,
        decision=MaintenanceDecision.APPROVE,
    )
    reviewed = applying.record_consistency_review(
        applied_document_version_ids=(APPLIED_VERSION_ID,),
    )

    corrective = reviewed.route_consistency_review(
        review_outcome=MaintenanceReviewOutcome.BLOCKING,
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )

    assert corrective.status is ProjectMaintenanceStatus.REVISION_PLAN
    assert corrective.applied_document_version_ids == (APPLIED_VERSION_ID,)
    assert corrective.consistency_report_id == CONSISTENCY_REPORT_ID
    assert corrective.revision_plan_document_id is None
    assert corrective.revision_plan_version_id is None


def test_corrective_revision_confirmation_cannot_cancel_without_rollback_support() -> None:
    corrective = state(ProjectMaintenanceStatus.CONSISTENCY_REVIEW).route_consistency_review(
        review_outcome=MaintenanceReviewOutcome.BLOCKING,
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )
    planned = corrective.record_revision_plan(
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
    )
    waiting = planned.request_revision_confirmation(
        action_request_id=ACTION_ID,
        review_outcome=MaintenanceReviewOutcome.PASSED,
    )

    with pytest.raises(ProjectMaintenanceValidationError):
        waiting.resolve_confirmation(
            live_action_request_id=ACTION_ID,
            action_status=ActionRequestStatus.PENDING,
            decision=MaintenanceDecision.CANCEL,
        )


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.CHANGE_REQUESTED).record_lore_impact(
                lore_impact_report_id=LORE_REPORT_ID
            ),
            id="lore-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS).record_lore_impact(
                lore_impact_report_id="00000000-0000-0000-0000-000000000000"
            ),
            id="lore-invalid-report",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS).record_chief_impact(
                chief_impact_report_id=CHIEF_REPORT_ID
            ),
            id="chief-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS).record_chief_impact(
                chief_impact_report_id="not-a-uuid"
            ),
            id="chief-invalid-report",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS).record_revision_plan(
                revision_plan_document_id=REVISION_PLAN_ID,
                revision_plan_version_id=REVISION_PLAN_VERSION_ID,
            ),
            id="plan-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.REVISION_PLAN).record_revision_plan(
                revision_plan_document_id="not-a-uuid",
                revision_plan_version_id=REVISION_PLAN_VERSION_ID,
            ),
            id="plan-invalid-document",
        ),
        pytest.param(
            lambda: state(
                ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS
            ).request_revision_confirmation(
                action_request_id=ACTION_ID,
                review_outcome=MaintenanceReviewOutcome.PASSED,
            ),
            id="confirmation-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.REVISION_PLAN).request_revision_confirmation(
                action_request_id="00000000-0000-0000-0000-000000000000",
                review_outcome=MaintenanceReviewOutcome.PASSED,
            ),
            id="confirmation-invalid-action",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.REVISION_PLAN).record_consistency_review(
                applied_document_version_ids=(APPLIED_VERSION_ID,)
            ),
            id="consistency-record-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.APPLY_CHANGE).record_consistency_review(
                applied_document_version_ids=()
            ),
            id="consistency-record-empty-applied",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.APPLY_CHANGE).route_consistency_review(
                review_outcome=MaintenanceReviewOutcome.PASSED,
                consistency_report_id=CONSISTENCY_REPORT_ID,
            ),
            id="consistency-route-wrong-source",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.CONSISTENCY_REVIEW).route_consistency_review(
                review_outcome=MaintenanceReviewOutcome.WARNING,
                consistency_report_id=CONSISTENCY_REPORT_ID,
            ),
            id="consistency-warning-missing-action",
        ),
        pytest.param(
            lambda: state(ProjectMaintenanceStatus.CONSISTENCY_REVIEW).route_consistency_review(
                review_outcome=MaintenanceReviewOutcome.PASSED,
                consistency_report_id=CONSISTENCY_REPORT_ID,
                action_request_id=ACTION_ID,
            ),
            id="consistency-passed-with-action",
        ),
    ],
)
def test_specialized_methods_fail_closed_for_wrong_source_or_invalid_arguments(
    operation: Callable[[], object],
) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        operation()


def test_passed_consistency_review_routes_to_project_updated() -> None:
    updated = state(ProjectMaintenanceStatus.CONSISTENCY_REVIEW).route_consistency_review(
        review_outcome=MaintenanceReviewOutcome.PASSED,
        consistency_report_id=CONSISTENCY_REPORT_ID,
    )

    assert updated.status is ProjectMaintenanceStatus.PROJECT_UPDATED
    assert updated.is_terminal


@pytest.mark.parametrize(
    ("decision", "review_outcome", "expected"),
    [
        (
            MaintenanceDecision.APPROVE,
            MaintenanceReviewOutcome.PASSED,
            ProjectMaintenanceStatus.APPLY_CHANGE,
        ),
        (
            MaintenanceDecision.APPROVE,
            MaintenanceReviewOutcome.WARNING,
            ProjectMaintenanceStatus.APPLY_CHANGE,
        ),
        (
            MaintenanceDecision.REVISE,
            MaintenanceReviewOutcome.BLOCKING,
            ProjectMaintenanceStatus.REVISION_PLAN,
        ),
        (
            MaintenanceDecision.REVISE,
            MaintenanceReviewOutcome.PASSED,
            ProjectMaintenanceStatus.REVISION_PLAN,
        ),
        (
            MaintenanceDecision.REVISE,
            MaintenanceReviewOutcome.WARNING,
            ProjectMaintenanceStatus.REVISION_PLAN,
        ),
        (
            MaintenanceDecision.CANCEL,
            MaintenanceReviewOutcome.BLOCKING,
            ProjectMaintenanceStatus.CANCELLED,
        ),
        (
            MaintenanceDecision.CANCEL,
            MaintenanceReviewOutcome.PASSED,
            ProjectMaintenanceStatus.CANCELLED,
        ),
        (
            MaintenanceDecision.CANCEL,
            MaintenanceReviewOutcome.WARNING,
            ProjectMaintenanceStatus.CANCELLED,
        ),
    ],
)
def test_live_confirmation_decisions_use_the_only_allowed_targets(
    decision: MaintenanceDecision,
    review_outcome: MaintenanceReviewOutcome,
    expected: ProjectMaintenanceStatus,
) -> None:
    resolved = state(
        ProjectMaintenanceStatus.USER_CONFIRMATION, gate_outcome=review_outcome
    ).resolve_confirmation(
        live_action_request_id=ACTION_ID,
        action_status=ActionRequestStatus.PENDING,
        decision=decision,
    )

    assert resolved.status is expected
    assert resolved.action_request_id is None
    assert resolved.awaiting_user is False


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (MaintenanceDecision.ACCEPT_WARNING, ProjectMaintenanceStatus.PROJECT_UPDATED),
        (MaintenanceDecision.REVISE, ProjectMaintenanceStatus.REVISION_PLAN),
    ],
)
def test_consistency_warning_confirmation_has_separate_decisions_and_targets(
    decision: MaintenanceDecision, expected: ProjectMaintenanceStatus
) -> None:
    waiting = state(ProjectMaintenanceStatus.CONSISTENCY_REVIEW).route_consistency_review(
        review_outcome=MaintenanceReviewOutcome.WARNING,
        consistency_report_id=CONSISTENCY_REPORT_ID,
        action_request_id=ACTION_ID,
    )

    assert waiting.confirmation_kind is MaintenanceConfirmationKind.CONSISTENCY_WARNING
    assert (
        waiting.resolve_confirmation(
            live_action_request_id=ACTION_ID,
            action_status=ActionRequestStatus.PENDING,
            decision=decision,
        ).status
        is expected
    )


@pytest.mark.parametrize(
    ("decision", "review_outcome"),
    [
        (MaintenanceDecision.APPROVE, MaintenanceReviewOutcome.BLOCKING),
        (MaintenanceDecision.ACCEPT_WARNING, MaintenanceReviewOutcome.BLOCKING),
        (MaintenanceDecision.ACCEPT_WARNING, MaintenanceReviewOutcome.PASSED),
        (MaintenanceDecision.ACCEPT_WARNING, MaintenanceReviewOutcome.WARNING),
    ],
)
def test_blocking_and_warning_outcomes_cannot_be_bypassed(
    decision: MaintenanceDecision, review_outcome: MaintenanceReviewOutcome
) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        state(
            ProjectMaintenanceStatus.USER_CONFIRMATION, gate_outcome=review_outcome
        ).resolve_confirmation(
            live_action_request_id=ACTION_ID,
            action_status=ActionRequestStatus.PENDING,
            decision=decision,
        )


@pytest.mark.parametrize(
    ("live_action_request_id", "action_status"),
    [
        ("b91e7c68-82a1-45f0-ac25-c92378e011ed", ActionRequestStatus.PENDING),
        *[
            (ACTION_ID, status)
            for status in ActionRequestStatus
            if status is not ActionRequestStatus.PENDING
        ],
    ],
)
def test_stale_or_replayed_confirmation_fails_closed(
    live_action_request_id: str, action_status: ActionRequestStatus
) -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        state(ProjectMaintenanceStatus.USER_CONFIRMATION).resolve_confirmation(
            live_action_request_id=live_action_request_id,
            action_status=action_status,
            decision=MaintenanceDecision.APPROVE,
        )


def test_apply_change_cannot_be_entered_through_the_generic_transition_api() -> None:
    with pytest.raises(ProjectMaintenanceValidationError):
        state(ProjectMaintenanceStatus.USER_CONFIRMATION).transition_to(
            ProjectMaintenanceStatus.APPLY_CHANGE
        )


def test_public_event_payload_is_an_allowlist_without_content_or_references() -> None:
    waiting = ProjectMaintenanceState(
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        "user_confirm_revision",
        True,
        ACTION_ID,
        MaintenanceConfirmationKind.REVISION_CONFIRMATION,
        gate_review_outcome=MaintenanceReviewOutcome.PASSED,
        affected_item_ids=(AFFECTED_ITEM_ID,),
        lore_impact_report_id=LORE_REPORT_ID,
        chief_impact_report_id=CHIEF_REPORT_ID,
        revision_plan_document_id=REVISION_PLAN_ID,
        revision_plan_version_id=REVISION_PLAN_VERSION_ID,
    )

    assert waiting.to_public_event_payload() == {
        "status": "USER_CONFIRMATION",
        "current_node": "user_confirm_revision",
        "awaiting_user": True,
        "action_request_id": ACTION_ID,
        "confirmation_kind": "revision_confirmation",
    }
