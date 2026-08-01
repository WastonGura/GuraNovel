from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.maintenance_contracts import (
    ChiefEditorMaintenanceImpactOutput,
    ImpactAffectedItem,
    LoreImpactOutput,
    RevisionOperation,
    RevisionPlanOutput,
)
from app.core.errors import ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    ActionRequest,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    MaintenanceAffectedItem,
    MaintenanceChange,
    Project,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.services import ProjectService
from app.services.document_service import DocumentCommitIndeterminateError, DocumentService
from app.services.project_maintenance_foundation_service import (
    ProjectMaintenanceCommitIndeterminateError,
    ProjectMaintenanceFoundationService,
)
from app.services.project_maintenance_service import (
    ProjectMaintenanceComposition,
    ProjectMaintenanceService,
)
from app.workspace import ProjectWorkspace
from app.workflows.project_maintenance import MaintenanceDecision


class _ImpactAgent:
    def __init__(self, *, chief: bool = False, fail: bool = False) -> None:
        self.chief = chief
        self.fail = fail

    async def analyze(self, request: object) -> object:
        if self.fail:
            raise RuntimeError("sk-not-a-real-key private provider response")
        document = request.document_refs[0]  # type: ignore[attr-defined]
        common = {
            "affected_items": (
                ImpactAffectedItem(
                    stable_reference="world/core-rule",
                    item_type="world",
                    impact_level="high" if self.chief else "medium",
                    document=document,
                    reason="The current world rule is affected.",
                ),
            ),
            "impact_summary": "One canonical document is affected.",
            "safe_to_change": True,
        }
        if self.chief:
            return ChiefEditorMaintenanceImpactOutput(
                **common,
                reader_expectation_impact="medium",
                commercial_impact="low",
            )
        return LoreImpactOutput(**common)


class _PlanAgent:
    async def plan(self, request: object) -> RevisionPlanOutput:
        affected = request.affected_items[0]  # type: ignore[attr-defined]
        return RevisionPlanOutput(
            plan_id=uuid4(),
            summary="Revise the affected canonical document.",
            operations=(
                RevisionOperation(
                    operation_id=uuid4(),
                    sequence=1,
                    operation="revise",
                    target=affected.document,
                    affected_item_ids=(affected.affected_item_id,),
                    instruction="Prepare a replacement while preserving version history.",
                ),
            ),
            safety={
                "requires_user_confirmation": True,
                "preserve_existing_versions": True,
                "direct_write_authority": False,
            },
        )


class _CallbackPlanAgent(_PlanAgent):
    def __init__(self, callback: Callable[[], Awaitable[None]]) -> None:
        self.callback = callback

    async def plan(self, request: object) -> RevisionPlanOutput:
        await self.callback()
        return await super().plan(request)


def _composition(*, fail_lore: bool = False) -> ProjectMaintenanceComposition:
    return ProjectMaintenanceComposition(
        _ImpactAgent(fail=fail_lore),  # type: ignore[arg-type]
        _ImpactAgent(chief=True),  # type: ignore[arg-type]
        _PlanAgent(),  # type: ignore[arg-type]
        _PlanAgent(),  # type: ignore[arg-type]
    )


async def _project_with_document(session: AsyncSession, root: Path, suffix: str) -> Project:
    project = await ProjectService(session, ProjectWorkspace(root)).create_project(
        slug=f"maintenance-orchestrator-{suffix}-{uuid4().hex[:8]}",
        title="Maintenance orchestration",
    )
    await DocumentService(session).create_document(
        project_id=project.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="World rules",
        path="world/overview.md",
        content="# World\n\nThe old rule.",
        source=DocumentSource.USER,
        change_summary="Seed maintenance context.",
    )
    return project


async def _counts(session: AsyncSession) -> dict[type[object], int]:
    models = (
        WorkflowRun,
        MaintenanceChange,
        MaintenanceAffectedItem,
        ActionRequest,
        WorkflowCheckpoint,
        WorkflowEvent,
    )
    return {
        model: int(await session.scalar(select(func.count()).select_from(model)) or 0)
        for model in models
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_happy_path_persists_bound_confirmation_and_restarts_from_fresh_session(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "happy")
    started = await ProjectMaintenanceService(async_session, _composition()).start(
        project.id,
        title="Retcon the world rule",
        change_request="Adjust the rule without rewriting history.",
    )

    assert started.state.status.value == "USER_CONFIRMATION"
    assert started.state.action_request_id == str(started.action_request_id)
    assert await _counts(async_session) == {
        WorkflowRun: 1,
        MaintenanceChange: 1,
        MaintenanceAffectedItem: 1,
        ActionRequest: 1,
        WorkflowCheckpoint: 5,
        WorkflowEvent: 5,
    }
    action = await async_session.get(ActionRequest, started.action_request_id)
    assert action is not None
    assert action.status == "pending"
    assert action.options == ["approve", "revise", "cancel"]
    assert set(action.metadata_) == {"confirmation_kind", "review_outcome"}
    assert "Adjust the rule" not in str(action.metadata_)

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as fresh:
            loaded = await ProjectMaintenanceService(fresh, _composition()).load_waiting(
                project.id, started.workflow_run_id
            )
            assert loaded == started
            checkpoints = list(await fresh.scalars(select(WorkflowCheckpoint)))
            events = list(
                await fresh.scalars(
                    select(WorkflowEvent).order_by(WorkflowEvent.created_at, WorkflowEvent.id)
                )
            )
            assert [item.event_type for item in events] == [
                "project_maintenance_started",
                "project_maintenance_lore_analysis_started",
                "project_maintenance_impact_reconciled",
                "project_maintenance_revision_plan_created",
                "project_maintenance_confirmation_requested",
            ]
            assert all("Adjust the rule" not in str(item.state_json) for item in checkpoints)
            assert all("Adjust the rule" not in str(item.payload) for item in events)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_duplicate_start_and_cross_project_load_fail_closed(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    first = await _project_with_document(async_session, tmp_path / "first", "first")
    second = await _project_with_document(async_session, tmp_path / "second", "second")
    first_id, second_id = first.id, second.id
    service = ProjectMaintenanceService(async_session, _composition())
    started = await service.start(first_id, title="One", change_request="First change.")
    baseline = await _counts(async_session)

    with pytest.raises(ConflictError):
        await service.start(first_id, title="Two", change_request="Duplicate change.")
    assert await _counts(async_session) == baseline

    with pytest.raises(NotFoundError):
        await service.load_waiting(second_id, started.workflow_run_id)


@pytest.mark.integration
@pytest.mark.anyio
async def test_provider_failure_has_no_maintenance_rows_and_remains_safe(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "provider-failure")

    with pytest.raises(WorkflowStateError, match="plan could not be prepared") as error:
        await ProjectMaintenanceService(async_session, _composition(fail_lore=True)).start(
            project.id,
            title="Failure",
            change_request="private novel request",
        )

    assert "private provider" not in str(error.value)
    assert await _counts(async_session) == {
        WorkflowRun: 0,
        MaintenanceChange: 0,
        MaintenanceAffectedItem: 0,
        ActionRequest: 0,
        WorkflowCheckpoint: 0,
        WorkflowEvent: 0,
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_commit_acknowledgement_failure_is_indeterminate_and_rolls_back(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "commit-failure")

    async def fail_commit() -> None:
        raise RuntimeError("database acknowledgement detail")

    monkeypatch.setattr(async_session, "commit", fail_commit)
    with pytest.raises(ProjectMaintenanceCommitIndeterminateError) as error:
        await ProjectMaintenanceService(async_session, _composition()).start(
            project.id,
            title="Commit failure",
            change_request="Try one change.",
        )

    assert "acknowledgement detail" not in str(error.value)
    assert await _counts(async_session) == {
        WorkflowRun: 0,
        MaintenanceChange: 0,
        MaintenanceAffectedItem: 0,
        ActionRequest: 0,
        WorkflowCheckpoint: 0,
        WorkflowEvent: 0,
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_stale_current_version_race_fails_before_any_maintenance_rows(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "stale-version")
    document = await async_session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.type == DocumentType.WORLD_OVERVIEW.value,
        )
    )
    assert document is not None and document.current_version_id is not None
    document_id = document.id
    expected_version_id = document.current_version_id

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    racing_session = sessions()

    async def advance_document() -> None:
        await DocumentService(racing_session).write_document(
            document_id=document_id,
            content="# World\n\nThe concurrently updated rule.",
            source=DocumentSource.USER,
            expected_current_version_id=expected_version_id,
            change_summary="Concurrent author update.",
        )

    composition = ProjectMaintenanceComposition(
        _ImpactAgent(),  # type: ignore[arg-type]
        _ImpactAgent(chief=True),  # type: ignore[arg-type]
        _PlanAgent(),  # type: ignore[arg-type]
        _CallbackPlanAgent(advance_document),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(ConflictError, match="snapshot changed"):
            await ProjectMaintenanceService(async_session, composition).start(
                project.id,
                title="Raced maintenance",
                change_request="Revise the old current version.",
            )
        assert await _counts(async_session) == {
            WorkflowRun: 0,
            MaintenanceChange: 0,
            MaintenanceAffectedItem: 0,
            ActionRequest: 0,
            WorkflowCheckpoint: 0,
            WorkflowEvent: 0,
        }
    finally:
        await racing_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_snapshot_rows_remain_locked_through_maintenance_commit(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "snapshot-lock")
    document = await async_session.scalar(
        select(Document).where(
            Document.project_id == project.id,
            Document.type == DocumentType.WORLD_OVERVIEW.value,
        )
    )
    assert document is not None and document.current_version_id is not None
    document_id = document.id
    expected_version_id = document.current_version_id

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    maintenance_session = sessions()
    writer_session = sessions()
    maintenance = ProjectMaintenanceService(maintenance_session, _composition())
    writer = DocumentService(writer_session)
    snapshot_locked = asyncio.Event()
    release_commit = asyncio.Event()
    writer_attempted = asyncio.Event()
    original_revalidate = maintenance._revalidate_document_snapshot
    original_locked_document = writer._locked_document

    async def pause_after_snapshot_lock(*args: object, **kwargs: object) -> None:
        await original_revalidate(*args, **kwargs)  # type: ignore[arg-type]
        snapshot_locked.set()
        await release_commit.wait()

    async def observe_writer_lock(document_id_arg: object) -> Document:
        writer_attempted.set()
        return await original_locked_document(document_id_arg)  # type: ignore[arg-type]

    monkeypatch.setattr(maintenance, "_revalidate_document_snapshot", pause_after_snapshot_lock)
    monkeypatch.setattr(writer, "_locked_document", observe_writer_lock)
    start_task = asyncio.create_task(
        maintenance.start(
            project.id,
            title="Locked snapshot",
            change_request="Keep the validated snapshot stable through commit.",
        )
    )
    write_task: asyncio.Task[DocumentVersion] | None = None
    try:
        await asyncio.wait_for(snapshot_locked.wait(), timeout=5)
        write_task = asyncio.create_task(
            writer.write_document(
                document_id=document_id,
                content="# World\n\nThe post-maintenance concurrent rule.",
                source=DocumentSource.USER,
                expected_current_version_id=expected_version_id,
                change_summary="Concurrent author update after the gate snapshot.",
            )
        )
        await asyncio.wait_for(writer_attempted.wait(), timeout=5)
        await asyncio.sleep(0.1)
        assert not write_task.done()

        release_commit.set()
        started = await asyncio.wait_for(start_task, timeout=5)
        await asyncio.wait_for(write_task, timeout=5)

        with pytest.raises(WorkflowStateError, match="binding"):
            await ProjectMaintenanceService(async_session, _composition()).load_waiting(
                project.id, started.workflow_run_id
            )
    finally:
        release_commit.set()
        if not start_task.done():
            start_task.cancel()
        if write_task is not None and not write_task.done():
            write_task.cancel()
        await maintenance_session.close()
        await writer_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_restart_holds_workflow_lock_until_waiting_state_is_fully_loaded(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "restart-lock")
    started = await ProjectMaintenanceService(async_session, _composition()).start(
        project.id,
        title="Restart lock",
        change_request="Load the durable gate under one lock.",
    )

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    load_session = sessions()
    resolve_session = sessions()
    loader = ProjectMaintenanceService(load_session, _composition())
    resolver = ProjectMaintenanceFoundationService(resolve_session)
    loader_paused = asyncio.Event()
    release_loader = asyncio.Event()
    original_bound_report = loader._bound_impact_report
    calls = 0

    async def pause_during_bound_load(*args: object, **kwargs: object) -> ReviewReport | None:
        nonlocal calls
        report = await original_bound_report(*args, **kwargs)
        calls += 1
        if calls == 1:
            loader_paused.set()
            await release_loader.wait()
        return report

    monkeypatch.setattr(loader, "_bound_impact_report", pause_during_bound_load)
    load_task = asyncio.create_task(loader.load_waiting(project.id, started.workflow_run_id))
    resolve_task: asyncio.Task[object] | None = None
    try:
        await asyncio.wait_for(loader_paused.wait(), timeout=5)
        resolve_task = asyncio.create_task(
            resolver.resolve_action(
                project.id,
                started.workflow_run_id,
                started.action_request_id,
                decision=MaintenanceDecision.APPROVE,
            )
        )
        await asyncio.sleep(0.1)
        assert not resolve_task.done()

        release_loader.set()
        assert await asyncio.wait_for(load_task, timeout=5) == started
        resolved = await asyncio.wait_for(resolve_task, timeout=5)
        assert resolved.status.value == "APPLY_CHANGE"
    finally:
        release_loader.set()
        if not load_task.done():
            load_task.cancel()
        if resolve_task is not None and not resolve_task.done():
            resolve_task.cancel()
        await load_session.close()
        await resolve_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_session_concurrent_start_has_exactly_one_winner(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "concurrent")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_session = sessions()
    second_session = sessions()
    try:
        results = await asyncio.gather(
            ProjectMaintenanceService(first_session, _composition()).start(
                project.id,
                title="Concurrent one",
                change_request="First concurrent request.",
            ),
            ProjectMaintenanceService(second_session, _composition()).start(
                project.id,
                title="Concurrent two",
                change_request="Second concurrent request.",
            ),
            return_exceptions=True,
        )
        successes = [result for result in results if not isinstance(result, BaseException)]
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1 and isinstance(failures[0], ConflictError)

        await async_session.rollback()
        assert await _counts(async_session) == {
            WorkflowRun: 1,
            MaintenanceChange: 1,
            MaintenanceAffectedItem: 1,
            ActionRequest: 1,
            WorkflowCheckpoint: 5,
            WorkflowEvent: 5,
        }
    finally:
        await first_session.close()
        await second_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_commit_plan_file_failure_is_explicit_and_durable_state_is_reconcilable(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "file-failure")

    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise DocumentCommitIndeterminateError() from None

    monkeypatch.setattr(DocumentService, "write_staged_files", fail_file_write)
    with pytest.raises(DocumentCommitIndeterminateError):
        await ProjectMaintenanceService(async_session, _composition()).start(
            project.id,
            title="File failure",
            change_request="Persist before the file write fails.",
        )

    assert await _counts(async_session) == {
        WorkflowRun: 1,
        MaintenanceChange: 1,
        MaintenanceAffectedItem: 1,
        ActionRequest: 1,
        WorkflowCheckpoint: 5,
        WorkflowEvent: 5,
    }
    change = await async_session.scalar(
        select(MaintenanceChange).where(MaintenanceChange.project_id == project.id)
    )
    assert change is not None and change.revision_plan_document_id is not None
    plan_document = await async_session.get(Document, change.revision_plan_document_id)
    assert plan_document is not None and plan_document.current_version_id is not None
    plan_version = await async_session.get(DocumentVersion, plan_document.current_version_id)
    assert plan_version is not None and plan_version.snapshot_path is not None
    workspace = Path(project.workspace_root)
    assert not (workspace / plan_document.path).exists()
    assert not (workspace / plan_version.snapshot_path).exists()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as fresh:
            with pytest.raises(ProjectMaintenanceCommitIndeterminateError):
                await ProjectMaintenanceService(fresh, _composition()).load_waiting(
                    project.id, change.workflow_run_id
                )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["lore_report", "affected_item"])
async def test_restart_rejects_broken_impact_lineage(
    async_session: AsyncSession,
    tmp_path: Path,
    corruption: str,
) -> None:
    project = await _project_with_document(async_session, tmp_path, corruption)
    started = await ProjectMaintenanceService(async_session, _composition()).start(
        project.id,
        title="Lineage",
        change_request="Prepare a bound plan.",
    )
    if corruption == "lore_report":
        await async_session.execute(
            delete(ReviewReport).where(ReviewReport.id == started.state.lore_impact_report_id)
        )
    else:
        await async_session.execute(
            delete(MaintenanceAffectedItem).where(
                MaintenanceAffectedItem.maintenance_change_id == started.maintenance_change_id
            )
        )
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="binding"):
        await ProjectMaintenanceService(async_session, _composition()).load_waiting(
            project.id, started.workflow_run_id
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_restart_rejects_tampered_plan_snapshot(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "snapshot-tamper")
    started = await ProjectMaintenanceService(async_session, _composition()).start(
        project.id,
        title="Snapshot integrity",
        change_request="Keep the persisted plan identical across restart.",
    )
    version = await async_session.get(DocumentVersion, started.revision_plan_version_id)
    assert version is not None and version.snapshot_path is not None
    snapshot = Path(project.workspace_root) / version.snapshot_path
    content = snapshot.read_text(encoding="utf-8")
    tampered = content.replace(
        "Apply the reconciled project changes in canonical sequence.",
        "Silently redirect the reconciled project changes.",
    )
    assert tampered != content
    snapshot.write_text(tampered, encoding="utf-8")

    with pytest.raises(WorkflowStateError, match="binding"):
        await ProjectMaintenanceService(async_session, _composition()).load_waiting(
            project.id, started.workflow_run_id
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_restart_rejects_affected_item_target_mapping_corruption(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project = await _project_with_document(async_session, tmp_path, "mapping-tamper")
    await DocumentService(async_session).create_document(
        project_id=project.id,
        document_type=DocumentType.FULL_OUTLINE,
        title="Other document",
        path="plot/outline.md",
        content="# Plot\n\nAn unrelated current document.",
        source=DocumentSource.USER,
        change_summary="Seed a second maintenance target.",
    )
    started = await ProjectMaintenanceService(async_session, _composition()).start(
        project.id,
        title="Mapping integrity",
        change_request="Keep each affected item bound to its analyzed target.",
    )
    affected = await async_session.scalar(
        select(MaintenanceAffectedItem).where(
            MaintenanceAffectedItem.maintenance_change_id == started.maintenance_change_id
        )
    )
    assert affected is not None and affected.existing_document_id is not None
    other_document_id = await async_session.scalar(
        select(Document.id).where(
            Document.project_id == project.id,
            Document.id != affected.existing_document_id,
            Document.type != DocumentType.MAINTENANCE_PLAN.value,
        )
    )
    assert other_document_id is not None
    affected.existing_document_id = other_document_id
    await async_session.commit()

    with pytest.raises(WorkflowStateError, match="binding"):
        await ProjectMaintenanceService(async_session, _composition()).load_waiting(
            project.id, started.workflow_run_id
        )
