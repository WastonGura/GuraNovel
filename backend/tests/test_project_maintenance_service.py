from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.agents.maintenance_contracts import (
    ChiefEditorMaintenanceImpactOutput,
    DocumentVersionReference,
    ImpactAffectedItem,
    LoreImpactOutput,
    RevisionOperation,
    RevisionPlanOutput,
)
from app.core.errors import WorkflowStateError
from app.services.project_maintenance_service import (
    ProjectMaintenanceComposition,
    ProjectMaintenanceService,
    _decode_persisted_plan,
    _event,
    _prepare_plan,
    _reconcile_affected_items,
    _reconcile_operations,
    _safe_text,
)
from app.workflows.project_maintenance import ProjectMaintenanceState, ProjectMaintenanceStatus


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
CHANGE_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
AFFECTED_ID = UUID("66666666-6666-4666-8666-666666666666")


def test_maintenance_title_is_strictly_single_line() -> None:
    with pytest.raises(WorkflowStateError):
        _safe_text("line one\nline two", "Maintenance title", maximum=512, allow_multiline=False)


def _document_ref() -> DocumentVersionReference:
    return DocumentVersionReference(document_id=DOCUMENT_ID, current_version_id=VERSION_ID)


def _impact_item(*, level: str = "medium") -> ImpactAffectedItem:
    return ImpactAffectedItem(
        stable_reference="world/core-rule",
        item_type="world",
        impact_level=level,
        document=_document_ref(),
        reason="The canonical rule must remain internally consistent.",
    )


def _lore(*, level: str = "medium") -> LoreImpactOutput:
    return LoreImpactOutput(
        affected_items=(_impact_item(level=level),),
        impact_summary="One world rule is affected.",
        safe_to_change=True,
    )


def _chief(*, level: str = "high") -> ChiefEditorMaintenanceImpactOutput:
    return ChiefEditorMaintenanceImpactOutput(
        affected_items=(_impact_item(level=level),),
        impact_summary="Reader expectations remain manageable.",
        safe_to_change=True,
        reader_expectation_impact="medium",
        commercial_impact="low",
    )


def _plan(
    *, affected_id: UUID, plan_id: UUID | None = None, operation: str = "revise"
) -> RevisionPlanOutput:
    return RevisionPlanOutput(
        plan_id=plan_id or uuid4(),
        summary="Revise the canonical world rule.",
        operations=(
            RevisionOperation(
                operation_id=uuid4(),
                sequence=1,
                operation=operation,
                target=_document_ref(),
                affected_item_ids=(affected_id,),
                instruction="Prepare a replacement version while preserving history.",
            ),
        ),
        safety={
            "requires_user_confirmation": True,
            "preserve_existing_versions": True,
            "direct_write_authority": False,
        },
    )


def test_reconcile_affected_items_deduplicates_by_stable_reference_and_keeps_highest_impact() -> (
    None
):
    reconciled = _reconcile_affected_items(_lore(level="low"), _chief(level="high"))

    assert len(reconciled) == 1
    assert reconciled[0].id.int != 0
    assert reconciled[0].item.stable_reference == "world/core-rule"
    assert reconciled[0].item.impact_level.value == "high"


def test_reconcile_affected_items_fails_closed_on_conflicting_identity() -> None:
    chief = _chief().model_copy(
        update={"affected_items": (_impact_item().model_copy(update={"item_type": "plot"}),)}
    )

    with pytest.raises(WorkflowStateError, match="outputs conflict"):
        _reconcile_affected_items(_lore(), chief)


def test_reconcile_operations_merges_matching_provider_operations_without_trusting_ids() -> None:
    plot = _plan(affected_id=AFFECTED_ID)
    world = _plan(affected_id=AFFECTED_ID)

    operations = _reconcile_operations(
        plot,
        world,
        affected_item_ids=frozenset({AFFECTED_ID}),
    )

    assert len(operations) == 1
    assert operations[0].operation_id not in {
        plot.operations[0].operation_id,
        world.operations[0].operation_id,
    }
    assert operations[0].provider_operation_ids == (
        plot.operations[0].operation_id,
        world.operations[0].operation_id,
    )


def test_reconcile_operations_rejects_incomplete_impact_coverage() -> None:
    missing_id = uuid4()
    with pytest.raises(WorkflowStateError, match="does not cover"):
        _reconcile_operations(
            _plan(affected_id=AFFECTED_ID),
            _plan(affected_id=AFFECTED_ID),
            affected_item_ids=frozenset({AFFECTED_ID, missing_id}),
        )


@pytest.mark.parametrize("operation", ["retire", "retain"])
def test_reconcile_operations_rejects_plans_that_apply_change_cannot_consume(
    operation: str,
) -> None:
    with pytest.raises(WorkflowStateError, match="cannot be applied"):
        _reconcile_operations(
            _plan(affected_id=AFFECTED_ID, operation=operation),
            _plan(affected_id=AFFECTED_ID, operation=operation),
            affected_item_ids=frozenset({AFFECTED_ID}),
        )


def test_plan_renderer_and_public_event_never_include_transient_change_request() -> None:
    secret = "sk-not-a-real-key private novel premise"
    affected = _reconcile_affected_items(_lore(), _chief())
    plan = _prepare_plan(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        change_id=CHANGE_ID,
        affected=affected,
        lore=_lore(),
        chief=_chief(),
        plot=_plan(affected_id=affected[0].id),
        world=_plan(affected_id=affected[0].id),
    )
    state = ProjectMaintenanceState(
        ProjectMaintenanceStatus.CHANGE_REQUESTED,
        "user_change_request",
        False,
    )
    event = _event(RUN_ID, "project_maintenance_started", state)
    restored, _ = _decode_persisted_plan(
        plan.content,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        change_id=CHANGE_ID,
        outcome=plan.outcome,
    )

    assert secret not in plan.content
    assert restored.plan_id == plan.plan_id
    assert restored.operations[0].instruction
    assert secret not in str(state.to_checkpoint())
    assert secret not in str(event.payload)
    assert set(event.payload) == {"status", "current_node", "awaiting_user"}


class _ImpactAgent:
    def __init__(self, log: list[str], name: str, output: object) -> None:
        self.log = log
        self.name = name
        self.output = output

    async def analyze(self, request: object) -> object:
        self.log.append(self.name)
        return self.output


class _PlanAgent:
    def __init__(self, log: list[str], name: str) -> None:
        self.log = log
        self.name = name

    async def plan(self, request: object) -> RevisionPlanOutput:
        self.log.append(self.name)
        affected_id = request.affected_items[0].affected_item_id  # type: ignore[attr-defined]
        return _plan(affected_id=affected_id)


@pytest.mark.anyio
async def test_start_calls_agents_in_phase_order_then_persists_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    session = AsyncMock()
    service = ProjectMaintenanceService(
        session,
        ProjectMaintenanceComposition(
            _ImpactAgent(calls, "lore", _lore()),  # type: ignore[arg-type]
            _ImpactAgent(calls, "chief", _chief()),  # type: ignore[arg-type]
            _PlanAgent(calls, "plot"),  # type: ignore[arg-type]
            _PlanAgent(calls, "world"),  # type: ignore[arg-type]
        ),
    )
    snapshot = (_document_ref(),)
    load_snapshot = AsyncMock(return_value=snapshot)
    persist = AsyncMock(return_value=object())
    monkeypatch.setattr(service, "_load_document_snapshot", load_snapshot)
    monkeypatch.setattr(service, "_persist_start", persist)

    result = await service.start(PROJECT_ID, title="Retcon", change_request="Adjust the rule.")

    assert result is persist.return_value
    assert calls == ["lore", "chief", "plot", "world"]
    load_snapshot.assert_awaited_once_with(PROJECT_ID)
    persist.assert_awaited_once()
    assert session.rollback.await_count == 1


@pytest.mark.anyio
async def test_provider_failure_rolls_back_and_never_reaches_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingLore:
        async def analyze(self, request: object) -> object:
            raise RuntimeError("upstream secret response")

    session = AsyncMock()
    service = ProjectMaintenanceService(
        session,
        ProjectMaintenanceComposition(
            _FailingLore(),  # type: ignore[arg-type]
            _ImpactAgent([], "chief", _chief()),  # type: ignore[arg-type]
            _PlanAgent([], "plot"),  # type: ignore[arg-type]
            _PlanAgent([], "world"),  # type: ignore[arg-type]
        ),
    )
    monkeypatch.setattr(
        service, "_load_document_snapshot", AsyncMock(return_value=(_document_ref(),))
    )
    persist = AsyncMock()
    monkeypatch.setattr(service, "_persist_start", persist)

    with pytest.raises(WorkflowStateError, match="plan could not be prepared") as error:
        await service.start(PROJECT_ID, title="Retcon", change_request="private premise")

    assert "secret" not in str(error.value)
    assert session.rollback.await_count == 2
    persist.assert_not_awaited()
