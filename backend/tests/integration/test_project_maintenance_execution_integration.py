from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import (
    ApplyChangeOutput,
    ApplyChangeRequest,
    ConsistencyFinding,
    ConsistencyReviewOutput,
    DocumentVersionReference,
    PostChangeRequest,
    ProposedDocumentEdit,
    RevisionOperation,
    RevisionPlanOutput,
)
from app.agents.maintenance_contracts import (
    ChiefEditorMaintenanceImpactOutput,
    ImpactAffectedItem,
    LoreImpactOutput,
)
from app.core.errors import AppError, ConflictError, WorkflowStateError
from app.models import (
    ActionRequest,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
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
)
from app.services.project_maintenance_service import (
    ProjectMaintenanceComposition,
    ProjectMaintenanceService,
)
from app.workspace import ProjectWorkspace
from app.workspace.hashing import sha256_content
from app.workflows.project_maintenance import MaintenanceDecision


class _ExecutionLoreAgent:
    def __init__(self, *, outcome: str = "clean", fail_post_change: bool = False) -> None:
        self.outcome = outcome
        self.fail_post_change = fail_post_change
        self.post_change_calls: list[PostChangeRequest] = []

    async def analyze(self, request: object) -> LoreImpactOutput:
        items = tuple(
            ImpactAffectedItem(
                stable_reference=f"world/rule-{index}",
                item_type="world",
                impact_level="high",
                document=document,
                reason="The canonical rule must remain consistent.",
            )
            for index, document in enumerate(request.document_refs, start=1)  # type: ignore[attr-defined]
        )
        return LoreImpactOutput(
            affected_items=items,
            impact_summary="The canonical world rules are affected.",
            safe_to_change=True,
        )

    async def post_change(self, request: PostChangeRequest) -> ConsistencyReviewOutput:
        self.post_change_calls.append(request)
        if self.fail_post_change:
            raise RuntimeError("sk-private post-change provider payload")
        findings: tuple[ConsistencyFinding, ...] = ()
        if self.outcome != "clean":
            blocking = self.outcome == "blocking"
            findings = (
                ConsistencyFinding(
                    finding_id=uuid4(),
                    sequence=1,
                    code=f"maintenance_{self.outcome}",
                    severity=self.outcome,
                    affected_documents=tuple(
                        DocumentVersionReference(
                            document_id=item.document_id,
                            current_version_id=item.current_version_id,
                        )
                        for item in request.applied_changes
                    ),
                    blocking=blocking,
                    suggested_corrective_action=(
                        "Prepare a corrective revision for the affected canonical rules."
                    ),
                ),
            )
        return ConsistencyReviewOutput(
            review_id=uuid4(),
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            change_request_id=request.change_request_id,
            approval_id=request.approval_id,
            revision_plan_id=request.revision_plan_id,
            revision_plan_document_id=request.revision_plan_document_id,
            revision_plan_version_id=request.revision_plan_version_id,
            change_set_id=request.change_set_id,
            outcome=self.outcome,
            findings=findings,
        )


class _ExecutionChiefAgent:
    async def analyze(self, request: object) -> ChiefEditorMaintenanceImpactOutput:
        lore = await _ExecutionLoreAgent().analyze(request)
        return ChiefEditorMaintenanceImpactOutput(
            affected_items=lore.affected_items,
            impact_summary="The proposed changes preserve reader expectations.",
            safe_to_change=True,
            reader_expectation_impact="medium",
            commercial_impact="low",
        )


class _ExecutionPlanAgent:
    async def plan(self, request: object) -> RevisionPlanOutput:
        grouped: dict[tuple[UUID, UUID], list[UUID]] = {}
        for affected in request.affected_items:  # type: ignore[attr-defined]
            if affected.document is None:
                continue
            key = (affected.document.document_id, affected.document.current_version_id)
            grouped.setdefault(key, []).append(affected.affected_item_id)
        operations = tuple(
            RevisionOperation(
                operation_id=uuid4(),
                sequence=sequence,
                operation="revise",
                target=DocumentVersionReference(
                    document_id=document_id,
                    current_version_id=version_id,
                ),
                affected_item_ids=tuple(affected_ids),
                instruction="Replace the approved canonical content and preserve version history.",
            )
            for sequence, ((document_id, version_id), affected_ids) in enumerate(
                grouped.items(), start=1
            )
        )
        return RevisionPlanOutput(
            plan_id=uuid4(),
            summary="Revise every affected canonical document.",
            operations=operations,
            safety={
                "requires_user_confirmation": True,
                "preserve_existing_versions": True,
                "direct_write_authority": False,
            },
        )


class _ExecutionArchivistAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[ApplyChangeRequest] = []

    async def apply_change(self, request: ApplyChangeRequest) -> ApplyChangeOutput:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("C:\\private\\novel.md sk-private apply payload")
        edits = tuple(
            ProposedDocumentEdit(
                proposed_edit_id=uuid4(),
                sequence=sequence,
                project_id=request.project_id,
                workflow_run_id=request.workflow_run_id,
                change_request_id=request.change_request_id,
                approval_id=request.approval_id,
                revision_plan_id=request.revision_plan_id,
                revision_plan_document_id=request.revision_plan_document_id,
                revision_plan_version_id=request.revision_plan_version_id,
                revision_operation_id=operation.operation_id,
                document_id=operation.target.document_id,
                expected_current_version_id=operation.target.current_version_id,
                operation="replace_content",
                content=(
                    f"# Revised canonical rule\n\nApproved maintenance replacement {sequence}.\n"
                ),
                rationale="Apply the exact approved revision operation.",
            )
            for sequence, operation in enumerate(
                (item for item in request.operations if item.operation.value == "revise"),
                start=1,
            )
        )
        return ApplyChangeOutput(
            change_set_id=uuid4(),
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            change_request_id=request.change_request_id,
            approval_id=request.approval_id,
            revision_plan_id=request.revision_plan_id,
            revision_plan_document_id=request.revision_plan_document_id,
            revision_plan_version_id=request.revision_plan_version_id,
            proposed_edits=edits,
        )


def _composition(
    *,
    outcome: str = "clean",
    fail_archivist: bool = False,
    fail_lore: bool = False,
) -> tuple[ProjectMaintenanceComposition, _ExecutionArchivistAgent, _ExecutionLoreAgent]:
    lore = _ExecutionLoreAgent(outcome=outcome, fail_post_change=fail_lore)
    archivist = _ExecutionArchivistAgent(fail=fail_archivist)
    composition = ProjectMaintenanceComposition(
        lore,  # type: ignore[arg-type]
        _ExecutionChiefAgent(),  # type: ignore[arg-type]
        _ExecutionPlanAgent(),  # type: ignore[arg-type]
        _ExecutionPlanAgent(),  # type: ignore[arg-type]
        archivist_agent=archivist,  # type: ignore[arg-type]
    )
    return composition, archivist, lore


async def _project_with_documents(
    session: AsyncSession, root: Path, suffix: str, *, count: int = 2
) -> tuple[Project, tuple[Document, ...]]:
    project = await ProjectService(session, ProjectWorkspace(root)).create_project(
        slug=f"maintenance-execution-{suffix}-{uuid4().hex[:8]}",
        title="Maintenance execution",
    )
    documents: list[Document] = []
    document_types = (DocumentType.WORLD_OVERVIEW, DocumentType.FULL_OUTLINE)
    document_paths = ("world/overview.md", "plot/outline.md")
    for index in range(count):
        document = await DocumentService(session).create_document(
            project_id=project.id,
            document_type=document_types[index],
            title=f"Canonical document {index + 1}",
            path=document_paths[index],
            content=f"# Canonical {index + 1}\n\nOriginal content {index + 1}.\n",
            source=DocumentSource.USER,
            change_summary="Seed the maintenance execution test.",
        )
        documents.append(document)
    return project, tuple(documents)


async def _start(
    session: AsyncSession,
    root: Path,
    suffix: str,
    *,
    composition: ProjectMaintenanceComposition | None = None,
    count: int = 2,
) -> tuple[Project, tuple[Document, ...], object, ProjectMaintenanceComposition]:
    project, documents = await _project_with_documents(session, root, suffix, count=count)
    selected = composition or _composition()[0]
    started = await ProjectMaintenanceService(session, selected).start(
        project.id,
        title="Revise canonical rules",
        change_request="Apply an auditable change to the canonical project documents.",
    )
    await session.refresh(project)
    for document in documents:
        await session.refresh(document)
    return project, documents, started, selected


async def _canonical_versions(session: AsyncSession, project_id: UUID) -> dict[UUID, UUID]:
    rows = (
        await session.execute(
            select(Document.id, Document.current_version_id).where(
                Document.project_id == project_id,
                Document.type.not_in(
                    [
                        DocumentType.MAINTENANCE_PLAN.value,
                        DocumentType.MAINTENANCE_REPORT.value,
                    ]
                ),
            )
        )
    ).all()
    return {document_id: version_id for document_id, version_id in rows if version_id is not None}


async def _pending_action(session: AsyncSession, run_id: UUID) -> ActionRequest:
    actions = list(
        await session.scalars(
            select(ActionRequest).where(
                ActionRequest.workflow_run_id == run_id,
                ActionRequest.status == "pending",
            )
        )
    )
    assert len(actions) == 1
    return actions[0]


async def _latest_state_json(session: AsyncSession, run_id: UUID) -> dict:
    checkpoint = await session.scalar(
        select(WorkflowCheckpoint)
        .where(WorkflowCheckpoint.workflow_run_id == run_id)
        .order_by(WorkflowCheckpoint.checkpoint_index.desc())
        .limit(1)
    )
    assert checkpoint is not None
    return checkpoint.state_json


@pytest.mark.integration
@pytest.mark.anyio
async def test_clean_full_lifecycle_writes_parented_versions_and_releases_project(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    composition, archivist, lore = _composition()
    project, documents, started, _ = await _start(
        async_session, tmp_path, "clean", composition=composition
    )
    original_versions = {item.id: item.current_version_id for item in documents}

    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )

    await async_session.rollback()
    project = await async_session.get(Project, project.id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)  # type: ignore[attr-defined]
    change = await async_session.scalar(
        select(MaintenanceChange).where(
            MaintenanceChange.workflow_run_id == started.workflow_run_id  # type: ignore[attr-defined]
        )
    )
    action = await async_session.get(ActionRequest, started.action_request_id)  # type: ignore[attr-defined]
    assert project is not None and project.current_workflow_id is None
    assert run is not None and (run.status, run.completed_at is not None) == (
        "PROJECT_UPDATED",
        True,
    )
    assert change is not None and (change.status, change.applied_at is not None) == (
        "PROJECT_UPDATED",
        True,
    )
    assert action is not None and (action.status, action.user_decision) == (
        "approved",
        "approve",
    )
    assert len(archivist.calls) == len(lore.post_change_calls) == 1

    current_versions = await _canonical_versions(async_session, project.id)
    assert set(current_versions) == set(original_versions)
    assert all(current_versions[item] != original_versions[item] for item in current_versions)
    applied = list(
        await async_session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.id.in_(current_versions.values()))
            .order_by(DocumentVersion.id)
        )
    )
    assert len(applied) == 2
    required_metadata = {
        "maintenance_change_id",
        "approval_action_id",
        "revision_plan_id",
        "revision_plan_document_id",
        "revision_plan_version_id",
        "change_set_id",
        "revision_operation_id",
        "proposed_edit_id",
    }
    for version in applied:
        assert version.parent_version_id == original_versions[version.document_id]
        assert (version.source, version.agent_role, version.workflow_run_id) == (
            DocumentSource.ARCHIVIST_AGENT.value,
            "archivist_agent",
            started.workflow_run_id,  # type: ignore[attr-defined]
        )
        assert required_metadata <= set(version.metadata_)
        assert version.metadata_["maintenance_change_id"] == str(change.id)
        document = await async_session.get(Document, version.document_id)
        assert document is not None and version.snapshot_path is not None
        current_content = (Path(project.workspace_root) / document.path).read_text(encoding="utf-8")
        snapshot_content = (Path(project.workspace_root) / version.snapshot_path).read_text(
            encoding="utf-8"
        )
        assert current_content == snapshot_content
        assert sha256_content(snapshot_content) == version.content_hash

    report = await async_session.scalar(
        select(ReviewReport).where(ReviewReport.review_mode == "maintenance_consistency")
    )
    assert report is not None
    assert (
        report.project_id,
        report.workflow_run_id,
        report.reviewer_agent_role,
        report.passed,
        report.target_document_id,
        report.target_version_id,
    ) == (project.id, run.id, "lore_agent", True, None, None)
    assert report.blocking_issues == report.warnings == []


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_releases_project_without_writing_canonical_versions(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, _, started, composition = await _start(async_session, tmp_path, "cancel")
    before = await _canonical_versions(async_session, project.id)

    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.CANCEL,
    )

    await async_session.rollback()
    assert await _canonical_versions(async_session, project.id) == before
    project = await async_session.get(Project, project.id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)  # type: ignore[attr-defined]
    change = await async_session.scalar(
        select(MaintenanceChange).where(
            MaintenanceChange.workflow_run_id == started.workflow_run_id  # type: ignore[attr-defined]
        )
    )
    assert project is not None and project.current_workflow_id is None
    assert run is not None and run.status == "CANCELLED" and run.completed_at is not None
    assert change is not None and change.status == "CANCELLED" and change.applied_at is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_initial_revise_creates_new_plan_version_and_gate_without_canonical_write(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    project, _, started, composition = await _start(async_session, tmp_path, "revise")
    before = await _canonical_versions(async_session, project.id)
    old_plan_version_id = started.revision_plan_version_id  # type: ignore[attr-defined]

    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.REVISE,
    )

    await async_session.rollback()
    assert await _canonical_versions(async_session, project.id) == before
    state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert state["status"] == "USER_CONFIRMATION"
    assert state["revision_plan_version_id"] != str(old_plan_version_id)
    new_plan = await async_session.get(DocumentVersion, UUID(state["revision_plan_version_id"]))
    assert new_plan is not None and new_plan.parent_version_id == old_plan_version_id
    old_action = await async_session.get(ActionRequest, started.action_request_id)  # type: ignore[attr-defined]
    new_action = await _pending_action(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert old_action is not None and old_action.status == "revised"
    assert new_action.id != old_action.id
    assert new_action.options == ["approve", "revise", "cancel"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_archivist_failure_leaves_apply_change_and_resume_finishes_once(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    failing, failing_archivist, _ = _composition(fail_archivist=True)
    project, _, started, _ = await _start(
        async_session, tmp_path, "apply-resume", composition=failing
    )
    project_id = project.id
    before = await _canonical_versions(async_session, project_id)
    with pytest.raises(AppError) as error:
        await ProjectMaintenanceService(async_session, failing).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert "private" not in str(error.value)
    assert len(failing_archivist.calls) == 1

    await async_session.rollback()
    assert (await _latest_state_json(async_session, started.workflow_run_id))["status"] == (  # type: ignore[attr-defined]
        "APPLY_CHANGE"
    )
    assert await _canonical_versions(async_session, project_id) == before

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    healthy, healthy_archivist, _ = _composition()
    try:
        async with sessions() as fresh:
            await ProjectMaintenanceService(fresh, healthy).resume_apply(
                project_id,
                started.workflow_run_id,  # type: ignore[attr-defined]
            )
        async with sessions() as verify:
            assert (await _latest_state_json(verify, started.workflow_run_id))["status"] == (  # type: ignore[attr-defined]
                "PROJECT_UPDATED"
            )
            assert len(await _canonical_versions(verify, project_id)) == 2
    finally:
        await engine.dispose()
    assert len(healthy_archivist.calls) == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_lore_failure_preserves_applied_versions_then_resume_consistency_only(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    failing, archivist, failing_lore = _composition(fail_lore=True)
    project, _, started, _ = await _start(
        async_session, tmp_path, "review-resume", composition=failing
    )
    project_id = project.id
    before = await _canonical_versions(async_session, project_id)
    with pytest.raises(AppError) as error:
        await ProjectMaintenanceService(async_session, failing).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert "private" not in str(error.value)
    assert len(archivist.calls) == len(failing_lore.post_change_calls) == 1

    await async_session.rollback()
    applied = await _canonical_versions(async_session, project_id)
    assert applied.keys() == before.keys() and applied != before
    state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert state["status"] == "CONSISTENCY_REVIEW"
    version_count = int(
        await async_session.scalar(select(func.count()).select_from(DocumentVersion)) or 0
    )

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    healthy, resume_archivist, healthy_lore = _composition()
    try:
        async with sessions() as fresh:
            await ProjectMaintenanceService(fresh, healthy).resume_consistency(
                project_id,
                started.workflow_run_id,  # type: ignore[attr-defined]
            )
        async with sessions() as verify:
            assert (await _latest_state_json(verify, started.workflow_run_id))["status"] == (  # type: ignore[attr-defined]
                "PROJECT_UPDATED"
            )
            assert (
                int(await verify.scalar(select(func.count()).select_from(DocumentVersion)) or 0)
                == version_count
            )
    finally:
        await engine.dispose()
    assert resume_archivist.calls == []
    assert len(healthy_lore.post_change_calls) == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_warning_creates_exact_gate_and_accept_warning_releases_project(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    composition, _, _ = _composition(outcome="warning")
    project, _, started, _ = await _start(
        async_session, tmp_path, "warning-accept", composition=composition
    )
    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )

    await async_session.rollback()
    warning_action = await _pending_action(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert warning_action.request_type == "project_maintenance_consistency_warning"
    assert warning_action.options == ["accept_warning", "revise"]
    report = await async_session.scalar(
        select(ReviewReport).where(
            ReviewReport.workflow_run_id == started.workflow_run_id,  # type: ignore[attr-defined]
            ReviewReport.review_mode == "maintenance_consistency",
        )
    )
    assert report is not None and report.passed and report.warnings and not report.blocking_issues

    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        warning_action.id,
        decision=MaintenanceDecision.ACCEPT_WARNING,
    )
    await async_session.rollback()
    project = await async_session.get(Project, project.id)
    assert project is not None and project.current_workflow_id is None
    assert (await _latest_state_json(async_session, started.workflow_run_id))["status"] == (  # type: ignore[attr-defined]
        "PROJECT_UPDATED"
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_accept_warning_revalidates_applied_versions_before_releasing_project(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    composition, _, _ = _composition(outcome="warning")
    project, _, started, _ = await _start(
        async_session, tmp_path, "warning-stale", composition=composition, count=1
    )
    project_id = project.id
    run_id = started.workflow_run_id  # type: ignore[attr-defined]
    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project_id,
        run_id,
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )
    await async_session.rollback()
    warning_action = await _pending_action(async_session, run_id)
    warning_action_id = warning_action.id
    applied = await _canonical_versions(async_session, project_id)
    document_id, applied_version_id = next(iter(applied.items()))
    await DocumentService(async_session).write_document(
        document_id=document_id,
        content="# Canonical\n\nConcurrent revision after consistency review.\n",
        source=DocumentSource.USER,
        expected_current_version_id=applied_version_id,
        change_summary="Advance the reviewed document before warning acceptance.",
    )

    with pytest.raises((ConflictError, WorkflowStateError)):
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            run_id,
            warning_action_id,
            decision=MaintenanceDecision.ACCEPT_WARNING,
        )

    await async_session.rollback()
    state = await _latest_state_json(async_session, run_id)
    action = await async_session.get(ActionRequest, warning_action_id)
    project = await async_session.get(Project, project_id)
    assert state["status"] == "USER_CONFIRMATION"
    assert action is not None and action.status == "pending" and action.resolved_at is None
    assert project is not None and project.current_workflow_id == run_id


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["warning", "blocking"])
async def test_nonclean_revision_preserves_applied_history_and_creates_corrective_gate(
    async_session: AsyncSession,
    tmp_path: Path,
    outcome: str,
) -> None:
    composition, _, _ = _composition(outcome=outcome)
    project, _, started, _ = await _start(
        async_session, tmp_path, f"corrective-{outcome}", composition=composition
    )
    project_id = project.id
    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project_id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )
    await async_session.rollback()
    applied = await _canonical_versions(async_session, project_id)
    if outcome == "warning":
        warning_action = await _pending_action(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            warning_action.id,
            decision=MaintenanceDecision.REVISE,
        )

    await async_session.rollback()
    assert await _canonical_versions(async_session, project_id) == applied
    state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert state["status"] == "USER_CONFIRMATION"
    assert set(state["applied_document_version_ids"]) == {
        str(version_id) for version_id in applied.values()
    }
    corrective_action = await _pending_action(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert corrective_action.request_type == "project_maintenance_revision_confirmation"
    assert "cancel" not in corrective_action.options
    assert "accept_warning" not in corrective_action.options
    report = await async_session.get(ReviewReport, UUID(state["consistency_report_id"]))
    assert report is not None
    if outcome == "blocking":
        assert not report.passed and report.blocking_issues and not report.warnings
    else:
        assert report.passed and report.warnings and not report.blocking_issues
    plan_version = await async_session.get(DocumentVersion, UUID(state["revision_plan_version_id"]))
    assert plan_version is not None
    assert plan_version.parent_version_id == started.revision_plan_version_id  # type: ignore[attr-defined]
    if outcome == "blocking":
        corrective_plan_version_id = plan_version.id
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            corrective_action.id,
            decision=MaintenanceDecision.REVISE,
        )
        await async_session.rollback()
        revised_state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
        assert revised_state["status"] == "USER_CONFIRMATION"
        assert revised_state["revision_plan_version_id"] != str(corrective_plan_version_id)
        revised_plan = await async_session.get(
            DocumentVersion, UUID(revised_state["revision_plan_version_id"])
        )
        assert revised_plan is not None
        assert revised_plan.parent_version_id == corrective_plan_version_id
        assert await _canonical_versions(async_session, project_id) == applied


@pytest.mark.integration
@pytest.mark.anyio
async def test_stale_target_and_replayed_action_fail_without_extra_versions(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    composition, _, _ = _composition()
    project, documents, started, _ = await _start(
        async_session, tmp_path, "stale-apply", composition=composition, count=1
    )
    project_id = project.id
    document = documents[0]
    expected = document.current_version_id
    assert expected is not None
    await DocumentService(async_session).write_document(
        document_id=document.id,
        content="# Canonical\n\nConcurrent author revision.\n",
        source=DocumentSource.USER,
        expected_current_version_id=expected,
        change_summary="Race the maintenance approval.",
    )
    count_before = int(
        await async_session.scalar(select(func.count()).select_from(DocumentVersion)) or 0
    )
    canonical_before = await _canonical_versions(async_session, project_id)

    with pytest.raises((ConflictError, WorkflowStateError)):
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert (
        int(await async_session.scalar(select(func.count()).select_from(DocumentVersion)) or 0)
        == count_before
    )
    waiting_state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    pending_action = await async_session.get(ActionRequest, started.action_request_id)  # type: ignore[attr-defined]
    assert waiting_state["status"] == "USER_CONFIRMATION"
    assert pending_action is not None and pending_action.status == "pending"
    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project_id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.REVISE,
    )
    await async_session.rollback()
    revised_state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert revised_state["status"] == "USER_CONFIRMATION"
    assert await _canonical_versions(async_session, project_id) == canonical_before

    clean_project, _, clean_started, clean_composition = await _start(
        async_session, tmp_path / "replay", "replay", count=1
    )
    service = ProjectMaintenanceService(async_session, clean_composition)
    await service.resolve_action(
        clean_project.id,
        clean_started.workflow_run_id,  # type: ignore[attr-defined]
        clean_started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )
    count_after_apply = int(
        await async_session.scalar(select(func.count()).select_from(DocumentVersion)) or 0
    )
    with pytest.raises((ConflictError, WorkflowStateError)):
        await service.resolve_action(
            clean_project.id,
            clean_started.workflow_run_id,  # type: ignore[attr-defined]
            clean_started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert (
        int(await async_session.scalar(select(func.count()).select_from(DocumentVersion)) or 0)
        == count_after_apply
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_two_concurrent_approvals_have_one_winner_and_one_applied_version(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, _, started, _ = await _start(async_session, tmp_path, "concurrent", count=1)
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_session, second_session = sessions(), sessions()
    first_composition, _, _ = _composition()
    second_composition, _, _ = _composition()
    try:
        results = await asyncio.gather(
            ProjectMaintenanceService(first_session, first_composition).resolve_action(
                project.id,
                started.workflow_run_id,  # type: ignore[attr-defined]
                started.action_request_id,  # type: ignore[attr-defined]
                decision=MaintenanceDecision.APPROVE,
            ),
            ProjectMaintenanceService(second_session, second_composition).resolve_action(
                project.id,
                started.workflow_run_id,  # type: ignore[attr-defined]
                started.action_request_id,  # type: ignore[attr-defined]
                decision=MaintenanceDecision.APPROVE,
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, BaseException) for item in results) == 1
        assert sum(isinstance(item, (ConflictError, WorkflowStateError)) for item in results) == 1
        async with sessions() as verify:
            current = await _canonical_versions(verify, project.id)
            versions = int(
                await verify.scalar(
                    select(func.count())
                    .select_from(DocumentVersion)
                    .where(DocumentVersion.document_id.in_(current))
                )
                or 0
            )
            assert versions == 2
    finally:
        await first_session.close()
        await second_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_apply_commit_acknowledgement_failure_never_materializes_files(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composition, _, lore = _composition()
    project, documents, started, _ = await _start(
        async_session, tmp_path, "commit-indeterminate", composition=composition, count=1
    )
    document = documents[0]
    project_id = project.id
    document_path = Path(project.workspace_root) / document.path
    old_content = document_path.read_text(encoding="utf-8")
    original_commit = async_session.commit
    commit_calls = 0

    async def fail_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            await original_commit()
            return
        raise RuntimeError("private database acknowledgement")

    monkeypatch.setattr(async_session, "commit", fail_commit)
    with pytest.raises(ProjectMaintenanceCommitIndeterminateError) as error:
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert "private" not in str(error.value)
    assert commit_calls == 2
    assert document_path.read_text(encoding="utf-8") == old_content
    assert lore.post_change_calls == []


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_commit_file_failure_keeps_durable_apply_and_skips_lore(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composition, _, lore = _composition()
    project, documents, started, _ = await _start(
        async_session, tmp_path, "file-indeterminate", composition=composition, count=1
    )
    project_id = project.id
    document_id = documents[0].id
    old_version_id = documents[0].current_version_id

    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise OSError("C:\\private\\workspace\\novel.md")

    monkeypatch.setattr(DocumentService, "write_staged_files", fail_file_write)
    with pytest.raises(DocumentCommitIndeterminateError) as error:
        await ProjectMaintenanceService(async_session, composition).resolve_action(
            project_id,
            started.workflow_run_id,  # type: ignore[attr-defined]
            started.action_request_id,  # type: ignore[attr-defined]
            decision=MaintenanceDecision.APPROVE,
        )
    assert "private" not in str(error.value)
    assert lore.post_change_calls == []

    await async_session.rollback()
    state = await _latest_state_json(async_session, started.workflow_run_id)  # type: ignore[attr-defined]
    assert state["status"] == "CONSISTENCY_REVIEW"
    current = await _canonical_versions(async_session, project_id)
    assert current[document_id] != old_version_id
    change = await async_session.scalar(
        select(MaintenanceChange).where(
            MaintenanceChange.workflow_run_id == started.workflow_run_id  # type: ignore[attr-defined]
        )
    )
    assert change is not None and change.applied_at is not None


@pytest.mark.integration
@pytest.mark.anyio
async def test_public_execution_events_never_contain_content_paths_or_provider_failures(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    composition, _, _ = _composition()
    project, _, started, _ = await _start(
        async_session, tmp_path, "event-redaction", composition=composition, count=1
    )
    await ProjectMaintenanceService(async_session, composition).resolve_action(
        project.id,
        started.workflow_run_id,  # type: ignore[attr-defined]
        started.action_request_id,  # type: ignore[attr-defined]
        decision=MaintenanceDecision.APPROVE,
    )
    events = list(
        await async_session.scalars(
            select(WorkflowEvent).where(
                WorkflowEvent.workflow_run_id == started.workflow_run_id  # type: ignore[attr-defined]
            )
        )
    )
    rendered = str([item.payload for item in events])
    assert "Approved maintenance replacement" not in rendered
    assert "workspace" not in rendered.lower()
    assert "novel.md" not in rendered
    assert all(
        set(item.payload)
        <= {
            "status",
            "current_node",
            "awaiting_user",
            "action_request_id",
            "confirmation_kind",
        }
        for item in events
    )
