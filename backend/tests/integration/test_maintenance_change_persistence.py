from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    Chapter,
    Document,
    MaintenanceAffectedItem,
    MaintenanceChange,
    Project,
    WorkflowRun,
    WorkflowType,
)
from app.services.maintenance_change_service import (
    MaintenanceAffectedItemCreate,
    MaintenanceChangeService,
)
from app.workflows.project_maintenance import (
    AffectedItemType,
    ImpactLevel,
    ProjectMaintenanceStatus,
)

_NODES = {
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


async def seed_project(
    session: AsyncSession, suffix: str
) -> tuple[Project, WorkflowRun, Document, Chapter]:
    project = Project(
        slug=f"maintenance-{suffix}-{uuid4()}",
        title=suffix,
        workspace_root=f"/tmp/{suffix}",
    )
    session.add(project)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id,
        workflow_type=WorkflowType.PROJECT_MAINTENANCE.value,
        status=ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
        current_node=_NODES[ProjectMaintenanceStatus.CHANGE_REQUESTED],
        next_node=None,
        awaiting_user=False,
    )
    chapter = Chapter(project_id=project.id, chapter_number=1)
    document = Document(
        project_id=project.id,
        chapter=chapter,
        type="maintenance_plan",
        path=f"plans/{suffix}.md",
    )
    session.add_all((run, chapter, document))
    await session.flush()
    project.current_workflow_id = run.id
    await session.commit()
    return project, run, document, chapter


async def seed_bare_project(session: AsyncSession, suffix: str) -> tuple[Project, WorkflowRun]:
    project = Project(
        slug=f"maintenance-bare-{suffix}-{uuid4()}",
        title=suffix,
        workspace_root=f"/tmp/bare-{suffix}",
    )
    session.add(project)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id,
        workflow_type=WorkflowType.PROJECT_MAINTENANCE.value,
        status=ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
        current_node=_NODES[ProjectMaintenanceStatus.CHANGE_REQUESTED],
        next_node=None,
        awaiting_user=False,
    )
    session.add(run)
    await session.flush()
    project.current_workflow_id = run.id
    await session.commit()
    return project, run


async def set_run_phase(
    session: AsyncSession,
    project_id: UUID,
    run_id: UUID,
    status: ProjectMaintenanceStatus,
) -> None:
    project = await session.get(Project, project_id)
    run = await session.get(WorkflowRun, run_id)
    assert project is not None and run is not None
    project.current_workflow_id = run.id
    run.status = status.value
    run.current_node = _NODES[status]
    run.next_node = None
    run.awaiting_user = status is ProjectMaintenanceStatus.USER_CONFIRMATION
    run.completed_at = (
        datetime.now(UTC)
        if status
        in {ProjectMaintenanceStatus.PROJECT_UPDATED, ProjectMaintenanceStatus.CANCELLED}
        else None
    )
    await session.commit()


def affected(
    document_id: UUID, chapter_id: UUID
) -> tuple[MaintenanceAffectedItemCreate, ...]:
    return (
        MaintenanceAffectedItemCreate(
            item_type=AffectedItemType.WORLD,
            stable_reference="world/rule",
            impact_level=ImpactLevel.HIGH,
            reason="Rule changes.",
            existing_document_id=document_id,
        ),
        MaintenanceAffectedItemCreate(
            item_type=AffectedItemType.CHAPTER,
            stable_reference="chapter/1",
            impact_level=ImpactLevel.MEDIUM,
            reason="Chapter changes.",
            existing_chapter_id=chapter_id,
        ),
    )


async def durable_snapshot(session: AsyncSession, project_id: UUID) -> dict[str, object]:
    changes = list(
        await session.scalars(
            select(MaintenanceChange)
            .where(MaintenanceChange.project_id == project_id)
            .order_by(MaintenanceChange.created_at, MaintenanceChange.id)
        )
    )
    change_ids = [change.id for change in changes]
    items = (
        list(
            await session.scalars(
                select(MaintenanceAffectedItem)
                .where(MaintenanceAffectedItem.maintenance_change_id.in_(change_ids))
                .order_by(
                    MaintenanceAffectedItem.maintenance_change_id,
                    MaintenanceAffectedItem.position,
                )
            )
        )
        if change_ids
        else []
    )
    return {
        "changes": [
            (
                change.id,
                change.project_id,
                change.workflow_run_id,
                change.title,
                change.original_change_request,
                change.status,
                change.revision_plan_document_id,
                change.applied_at,
                change.metadata_,
                change.created_at,
                change.updated_at,
            )
            for change in changes
        ],
        "items": [
            (
                item.id,
                item.maintenance_change_id,
                item.position,
                item.item_type,
                item.stable_reference,
                item.impact_level,
                item.reason,
                item.existing_document_id,
                item.existing_chapter_id,
                item.created_at,
            )
            for item in items
        ],
    }


async def advance_change(
    session: AsyncSession,
    change: MaintenanceChange,
    status: ProjectMaintenanceStatus,
    *,
    revision_plan_document_id: UUID | None,
    applied_at: datetime | None,
    metadata: object = None,
) -> MaintenanceChange:
    await set_run_phase(session, change.project_id, change.workflow_run_id, status)
    return await MaintenanceChangeService(session).update_change(
        project_id=change.project_id,
        change_id=change.id,
        expected_updated_at=change.updated_at,
        status=status,
        revision_plan_document_id=revision_plan_document_id,
        applied_at=applied_at,
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_clean_create_then_legal_lifecycle_and_ordered_items_round_trip(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    project, run, document, chapter = await seed_project(async_session, "lifecycle")
    service = MaintenanceChangeService(async_session)
    change = await service.create_change(
        project_id=project.id,
        workflow_run_id=run.id,
        title="Retcon",
        original_change_request="Change the magic rule.",
        metadata={"source": {"kind": "user"}},
    )
    assert (change.status, change.revision_plan_document_id, change.applied_at) == (
        ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
        None,
        None,
    )
    assert change.affected_items == []

    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        revision_plan_document_id=None,
        applied_at=None,
    )
    change = await service.replace_affected_items(
        project_id=project.id,
        change_id=change.id,
        expected_updated_at=change.updated_at,
        affected_items=affected(document.id, chapter.id),
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        revision_plan_document_id=None,
        applied_at=None,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.REVISION_PLAN,
        revision_plan_document_id=None,
        applied_at=None,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.REVISION_PLAN,
        revision_plan_document_id=document.id,
        applied_at=None,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        revision_plan_document_id=document.id,
        applied_at=None,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.APPLY_CHANGE,
        revision_plan_document_id=document.id,
        applied_at=None,
    )
    applied_at = datetime.now(UTC)
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.APPLY_CHANGE,
        revision_plan_document_id=document.id,
        applied_at=applied_at,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        revision_plan_document_id=document.id,
        applied_at=applied_at,
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.PROJECT_UPDATED,
        revision_plan_document_id=document.id,
        applied_at=applied_at,
        metadata={"done": True},
    )

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as fresh_session:
            loaded = await MaintenanceChangeService(fresh_session).get_change(
                project_id=project.id, change_id=change.id
            )
            assert [item.position for item in loaded.affected_items] == [0, 1]
            assert [item.stable_reference for item in loaded.affected_items] == [
                "world/rule",
                "chapter/1",
            ]
            assert (
                loaded.status,
                loaded.revision_plan_document_id,
                loaded.applied_at,
                loaded.metadata_,
            ) == (
                ProjectMaintenanceStatus.PROJECT_UPDATED.value,
                document.id,
                applied_at,
                {"done": True},
            )
            terminal_snapshot = await durable_snapshot(fresh_session, project.id)
        async with sessions() as reject_session:
            with pytest.raises(WorkflowStateError):
                await MaintenanceChangeService(reject_session).replace_affected_items(
                    project_id=project.id,
                    change_id=change.id,
                    expected_updated_at=change.updated_at,
                    affected_items=(),
                )
            await reject_session.rollback()
        async with sessions() as observer:
            assert await durable_snapshot(observer, project.id) == terminal_snapshot
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_rejects_cross_project_references_stale_updates_and_impossible_jumps_without_changes(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    first, first_run, _, _ = await seed_project(async_session, "isolation-first")
    second, _, second_document, second_chapter = await seed_project(
        async_session, "isolation-second"
    )
    change = await MaintenanceChangeService(async_session).create_change(
        project_id=first.id,
        workflow_run_id=first_run.id,
        title="Scoped",
        original_change_request="Stay in one project.",
    )
    change = await advance_change(
        async_session,
        change,
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        revision_plan_document_id=None,
        applied_at=None,
    )
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as observer:
            baseline = await durable_snapshot(observer, first.id)
        async with sessions() as reject_session:
            with pytest.raises(NotFoundError):
                await MaintenanceChangeService(reject_session).replace_affected_items(
                    project_id=first.id,
                    change_id=change.id,
                    expected_updated_at=change.updated_at,
                    affected_items=affected(second_document.id, second_chapter.id),
                )
            await reject_session.rollback()
        async with sessions() as observer:
            assert await durable_snapshot(observer, first.id) == baseline

        await set_run_phase(
            async_session,
            first.id,
            first_run.id,
            ProjectMaintenanceStatus.REVISION_PLAN,
        )
        async with sessions() as observer:
            before_jump = await durable_snapshot(observer, first.id)
        async with sessions() as reject_session:
            with pytest.raises(WorkflowStateError):
                await MaintenanceChangeService(reject_session).update_change(
                    project_id=first.id,
                    change_id=change.id,
                    expected_updated_at=change.updated_at,
                    status=ProjectMaintenanceStatus.REVISION_PLAN,
                    revision_plan_document_id=None,
                    applied_at=None,
                    metadata={},
                )
            await reject_session.rollback()
        async with sessions() as observer:
            assert await durable_snapshot(observer, first.id) == before_jump

        await set_run_phase(
            async_session,
            first.id,
            first_run.id,
            ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        )
        stale_timestamp = change.updated_at
        updated = await MaintenanceChangeService(async_session).update_change(
            project_id=first.id,
            change_id=change.id,
            expected_updated_at=change.updated_at,
            status=ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
            revision_plan_document_id=None,
            applied_at=None,
            metadata={},
        )
        await set_run_phase(
            async_session,
            first.id,
            first_run.id,
            ProjectMaintenanceStatus.REVISION_PLAN,
        )
        async with sessions() as observer:
            before_stale = await durable_snapshot(observer, first.id)
        async with sessions() as reject_session:
            with pytest.raises(ConflictError):
                await MaintenanceChangeService(reject_session).update_change(
                    project_id=first.id,
                    change_id=updated.id,
                    expected_updated_at=stale_timestamp,
                    status=ProjectMaintenanceStatus.REVISION_PLAN,
                    revision_plan_document_id=None,
                    applied_at=None,
                    metadata={},
                )
            await reject_session.rollback()
        async with sessions() as observer:
            assert await durable_snapshot(observer, first.id) == before_stale
            with pytest.raises(NotFoundError):
                await MaintenanceChangeService(observer).get_change(
                    project_id=second.id, change_id=change.id
                )
        async with sessions() as reject_session:
            with pytest.raises(NotFoundError):
                await MaintenanceChangeService(reject_session).update_change(
                    project_id=second.id,
                    change_id=change.id,
                    expected_updated_at=updated.updated_at,
                    status=ProjectMaintenanceStatus.REVISION_PLAN,
                    revision_plan_document_id=None,
                    applied_at=None,
                    metadata={},
                )
            await reject_session.rollback()

        stale_project, stale_run, _, _ = await seed_project(async_session, "stale-run")
        stale_run.current_node = "tampered_node"
        await async_session.commit()
        async with sessions() as reject_session:
            with pytest.raises(WorkflowStateError):
                await MaintenanceChangeService(reject_session).create_change(
                    project_id=stale_project.id,
                    workflow_run_id=stale_run.id,
                    title="Rejected",
                    original_change_request="Stale run.",
                )
            await reject_session.rollback()
        async with sessions() as observer:
            assert await durable_snapshot(observer, stale_project.id) == {
                "changes": [],
                "items": [],
            }
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_distinct_database_constraints_fail_closed_with_exact_rollback(
    async_session: AsyncSession,
) -> None:
    project, run, _, _ = await seed_project(async_session, "constraints")
    change = await MaintenanceChangeService(async_session).create_change(
        project_id=project.id,
        workflow_run_id=run.id,
        title="One",
        original_change_request="Only one per run.",
    )
    _, other_run, _, _ = await seed_project(async_session, "other-owner")
    late_phase_run = WorkflowRun(
        project_id=project.id,
        workflow_type=WorkflowType.PROJECT_MAINTENANCE.value,
        status=ProjectMaintenanceStatus.USER_CONFIRMATION.value,
        current_node=_NODES[ProjectMaintenanceStatus.USER_CONFIRMATION],
        awaiting_user=True,
    )
    async_session.add(late_phase_run)
    await async_session.commit()
    project_id, run_id, change_id, other_run_id = (
        project.id,
        run.id,
        change.id,
        other_run.id,
    )
    baseline = await durable_snapshot(async_session, project_id)

    candidates = [
        (
            MaintenanceChange(
                project_id=project_id,
                workflow_run_id=other_run_id,
                title="Cross project",
                original_change_request="Must fail.",
                status=ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
            ),
            "fk_maintenance_changes_project_run",
        ),
        (
            MaintenanceChange(
                project_id=project_id,
                workflow_run_id=run_id,
                title="Duplicate",
                original_change_request="Must fail.",
                status=ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
            ),
            "uq_maintenance_changes_workflow_run_id",
        ),
        (
            MaintenanceAffectedItem(
                maintenance_change_id=change_id,
                position=0,
                item_type="unknown",
                stable_reference="unknown/type",
                impact_level=ImpactLevel.LOW.value,
                reason="Must fail.",
            ),
            "ck_maintenance_affected_items_type",
        ),
        (
            MaintenanceAffectedItem(
                maintenance_change_id=change_id,
                position=0,
                item_type=AffectedItemType.WORLD.value,
                stable_reference="unknown/impact",
                impact_level="catastrophic",
                reason="Must fail.",
            ),
            "ck_maintenance_affected_items_impact",
        ),
        (
            MaintenanceChange(
                project_id=project_id,
                workflow_run_id=late_phase_run.id,
                title="Late without plan",
                original_change_request="Must fail.",
                status=ProjectMaintenanceStatus.USER_CONFIRMATION.value,
            ),
            "ck_maintenance_changes_late_plan",
        ),
    ]
    for candidate, constraint_name in candidates:
        async_session.add(candidate)
        with pytest.raises(IntegrityError) as error:
            await async_session.flush()
        assert constraint_name in str(error.value.orig)
        await async_session.rollback()
        assert await durable_snapshot(async_session, project_id) == baseline


@pytest.mark.integration
@pytest.mark.anyio
async def test_loaded_and_unloaded_project_and_run_deletes_cascade_maintenance_rows(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    seeds: list[tuple[UUID, UUID, UUID]] = []
    for suffix in ("loaded-run", "unloaded-run", "loaded-project", "unloaded-project"):
        project, run = await seed_bare_project(async_session, suffix)
        change = await MaintenanceChangeService(async_session).create_change(
            project_id=project.id,
            workflow_run_id=run.id,
            title=suffix,
            original_change_request="Delete behavior.",
        )
        seeds.append((project.id, run.id, change.id))

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            loaded_run = await session.scalar(
                select(WorkflowRun)
                .options(selectinload(WorkflowRun.maintenance_change))
                .where(WorkflowRun.id == seeds[0][1])
            )
            assert loaded_run is not None and loaded_run.maintenance_change is not None
            await session.delete(loaded_run)
            await session.commit()
        async with sessions() as session:
            await session.execute(delete(WorkflowRun).where(WorkflowRun.id == seeds[1][1]))
            await session.commit()
        async with sessions() as session:
            loaded_project = await session.scalar(
                select(Project)
                .options(
                    selectinload(Project.workflow_runs),
                    selectinload(Project.maintenance_changes),
                )
                .where(Project.id == seeds[2][0])
            )
            assert loaded_project is not None and loaded_project.maintenance_changes
            await session.delete(loaded_project)
            await session.commit()
        async with sessions() as session:
            await session.execute(delete(Project).where(Project.id == seeds[3][0]))
            await session.commit()
        async with sessions() as observer:
            for project_id, run_id, change_id in seeds:
                assert await observer.get(MaintenanceChange, change_id) is None
                assert await observer.get(WorkflowRun, run_id) is None
                if project_id in {seeds[2][0], seeds[3][0]}:
                    assert await observer.get(Project, project_id) is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_creation_for_one_run_persists_exactly_one(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    project, run, _, _ = await seed_project(async_session, "create-race")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as first, sessions() as second, sessions() as observer:
            first_at_commit = asyncio.Event()
            allow_first_commit = asyncio.Event()
            original_commit = first.commit

            async def gated_commit() -> None:
                first_at_commit.set()
                await allow_first_commit.wait()
                await original_commit()

            first.commit = gated_commit  # type: ignore[method-assign]
            first_task = asyncio.create_task(
                MaintenanceChangeService(first).create_change(
                    project_id=project.id,
                    workflow_run_id=run.id,
                    title="First",
                    original_change_request="Race.",
                )
            )
            await asyncio.wait_for(first_at_commit.wait(), timeout=5)
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(second_pid, int)
            second_task = asyncio.create_task(
                MaintenanceChangeService(second).create_change(
                    project_id=project.id,
                    workflow_run_id=run.id,
                    title="Second",
                    original_change_request="Race.",
                )
            )
            await wait_for_lock(observer, second_pid)
            allow_first_commit.set()
            assert isinstance(await asyncio.wait_for(first_task, timeout=5), MaintenanceChange)
            with pytest.raises(ConflictError):
                await asyncio.wait_for(second_task, timeout=5)
        async with sessions() as observer:
            assert len(
                list(
                    await observer.scalars(
                        select(MaintenanceChange).where(
                            MaintenanceChange.workflow_run_id == run.id
                        )
                    )
                )
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_updates_use_lock_and_stale_timestamp_guard(
    async_session: AsyncSession, integration_database_url: str
) -> None:
    project, run, first_document, _ = await seed_project(async_session, "update-race")
    second_document = Document(
        project_id=project.id,
        type="maintenance_plan",
        path="plans/update-race-second.md",
    )
    async_session.add(second_document)
    await async_session.commit()
    change = await MaintenanceChangeService(async_session).create_change(
        project_id=project.id,
        workflow_run_id=run.id,
        title="Race",
        original_change_request="Concurrent update.",
    )
    for status in (
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        ProjectMaintenanceStatus.REVISION_PLAN,
    ):
        change = await advance_change(
            async_session,
            change,
            status,
            revision_plan_document_id=None,
            applied_at=None,
        )
    expected_updated_at = change.updated_at

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as first, sessions() as second, sessions() as observer:
            first_at_commit = asyncio.Event()
            allow_first_commit = asyncio.Event()
            original_commit = first.commit

            async def gated_commit() -> None:
                first_at_commit.set()
                await allow_first_commit.wait()
                await original_commit()

            first.commit = gated_commit  # type: ignore[method-assign]
            first_task = asyncio.create_task(
                MaintenanceChangeService(first).update_change(
                    project_id=project.id,
                    change_id=change.id,
                    expected_updated_at=expected_updated_at,
                    status=ProjectMaintenanceStatus.REVISION_PLAN,
                    revision_plan_document_id=first_document.id,
                    applied_at=None,
                    metadata={"writer": "first"},
                )
            )
            await asyncio.wait_for(first_at_commit.wait(), timeout=5)
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(second_pid, int)
            second_task = asyncio.create_task(
                MaintenanceChangeService(second).update_change(
                    project_id=project.id,
                    change_id=change.id,
                    expected_updated_at=expected_updated_at,
                    status=ProjectMaintenanceStatus.REVISION_PLAN,
                    revision_plan_document_id=second_document.id,
                    applied_at=None,
                    metadata={"writer": "second"},
                )
            )
            await wait_for_lock(observer, second_pid)
            allow_first_commit.set()
            await asyncio.wait_for(first_task, timeout=5)
            with pytest.raises(ConflictError):
                await asyncio.wait_for(second_task, timeout=5)
        async with sessions() as observer:
            persisted = await MaintenanceChangeService(observer).get_change(
                project_id=project.id, change_id=change.id
            )
            assert (persisted.revision_plan_document_id, persisted.metadata_) == (
                first_document.id,
                {"writer": "first"},
            )
    finally:
        await engine.dispose()


async def wait_for_lock(observer: AsyncSession, pid: int) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        wait_event_type = await observer.scalar(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        )
        if wait_event_type == "Lock":
            return
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail("Concurrent request did not reach the PostgreSQL lock barrier.")
        await asyncio.sleep(0.01)
