"""Project-scoped persistence primitives for maintenance changes."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError, ConflictError, NotFoundError, WorkflowStateError
from app.models import (
    Chapter,
    Document,
    MaintenanceAffectedItem,
    MaintenanceChange,
    Project,
    WorkflowRun,
    WorkflowType,
)
from app.workflows.project_maintenance import (
    AffectedItemType,
    ImpactLevel,
    ProjectMaintenanceStatus,
)

_MAX_METADATA_BYTES = 8_192
_MAX_METADATA_DEPTH = 8
_MAX_METADATA_NODES = 4_096
_MAX_AFFECTED_ITEMS = 256
_MAX_TITLE_LENGTH = 512
_MAX_CHANGE_REQUEST_LENGTH = 131_072
_MAX_STABLE_REFERENCE_LENGTH = 2_048
_MAX_REASON_LENGTH = 16_384

_NODES_BY_STATUS = {
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

_TERMINAL_STATUSES = {
    ProjectMaintenanceStatus.PROJECT_UPDATED,
    ProjectMaintenanceStatus.CANCELLED,
}

_ALLOWED_TRANSITIONS = {
    (ProjectMaintenanceStatus.CHANGE_REQUESTED, ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS),
    (
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
    ),
    (
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
        ProjectMaintenanceStatus.REVISION_PLAN,
    ),
    (ProjectMaintenanceStatus.REVISION_PLAN, ProjectMaintenanceStatus.REVISION_PLAN),
    (ProjectMaintenanceStatus.REVISION_PLAN, ProjectMaintenanceStatus.USER_CONFIRMATION),
    (ProjectMaintenanceStatus.USER_CONFIRMATION, ProjectMaintenanceStatus.APPLY_CHANGE),
    (ProjectMaintenanceStatus.USER_CONFIRMATION, ProjectMaintenanceStatus.REVISION_PLAN),
    (ProjectMaintenanceStatus.USER_CONFIRMATION, ProjectMaintenanceStatus.CANCELLED),
    (ProjectMaintenanceStatus.USER_CONFIRMATION, ProjectMaintenanceStatus.PROJECT_UPDATED),
    (ProjectMaintenanceStatus.APPLY_CHANGE, ProjectMaintenanceStatus.APPLY_CHANGE),
    (ProjectMaintenanceStatus.APPLY_CHANGE, ProjectMaintenanceStatus.CONSISTENCY_REVIEW),
    (
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
    ),
    (ProjectMaintenanceStatus.CONSISTENCY_REVIEW, ProjectMaintenanceStatus.USER_CONFIRMATION),
    (ProjectMaintenanceStatus.CONSISTENCY_REVIEW, ProjectMaintenanceStatus.REVISION_PLAN),
    (ProjectMaintenanceStatus.CONSISTENCY_REVIEW, ProjectMaintenanceStatus.PROJECT_UPDATED),
}

_AFFECTED_ITEM_EDIT_STATUSES = {
    ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
    ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
    ProjectMaintenanceStatus.REVISION_PLAN,
}


class MaintenanceChangeValidationError(AppError):
    status_code = http_status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "maintenance_change_invalid"
    default_message = "The maintenance change is invalid."


class MaintenanceChangeCommitIndeterminateError(AppError):
    status_code = 500
    code = "maintenance_change_commit_indeterminate"
    default_message = "The maintenance change outcome could not be confirmed."


@dataclass(frozen=True)
class MaintenanceAffectedItemCreate:
    item_type: AffectedItemType
    stable_reference: str
    impact_level: ImpactLevel
    reason: str
    existing_document_id: UUID | None = None
    existing_chapter_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_type, AffectedItemType):
            raise MaintenanceChangeValidationError("Affected-item type is not typed.")
        if not isinstance(self.impact_level, ImpactLevel):
            raise MaintenanceChangeValidationError("Impact level is not typed.")
        _require_text(
            self.stable_reference,
            "Stable reference",
            max_length=_MAX_STABLE_REFERENCE_LENGTH,
            allow_multiline=False,
        )
        _require_text(
            self.reason,
            "Affected-item reason",
            max_length=_MAX_REASON_LENGTH,
            allow_multiline=True,
        )
        if self.existing_document_id is not None and not isinstance(
            self.existing_document_id, UUID
        ):
            raise MaintenanceChangeValidationError("Document reference is invalid.")
        if self.existing_chapter_id is not None and not isinstance(
            self.existing_chapter_id, UUID
        ):
            raise MaintenanceChangeValidationError("Chapter reference is invalid.")


class MaintenanceChangeService:
    """Own all ORM mutation for project-maintenance persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_change(
        self,
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        title: str,
        original_change_request: str,
        affected_items: tuple[MaintenanceAffectedItemCreate, ...] = (),
        revision_plan_document_id: UUID | None = None,
        metadata: object = None,
    ) -> MaintenanceChange:
        _require_text(title, "Title", max_length=_MAX_TITLE_LENGTH, allow_multiline=False)
        _require_text(
            original_change_request,
            "Original change request",
            max_length=_MAX_CHANGE_REQUEST_LENGTH,
            allow_multiline=True,
        )
        _validate_affected_items(affected_items)
        _validate_lifecycle(
            current_status=None,
            target_status=ProjectMaintenanceStatus.CHANGE_REQUESTED,
            current_revision_plan_document_id=None,
            target_revision_plan_document_id=revision_plan_document_id,
            current_applied_at=None,
            target_applied_at=None,
            has_affected_items=bool(affected_items),
        )
        normalized_metadata = self.normalize_metadata({} if metadata is None else metadata)

        project, run = await self._locked_project_run(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            expected_status=ProjectMaintenanceStatus.CHANGE_REQUESTED,
            allow_terminal=False,
        )
        existing = await self.session.scalar(
            select(MaintenanceChange.id).where(
                MaintenanceChange.workflow_run_id == workflow_run_id
            )
        )
        if existing is not None:
            raise ConflictError("A maintenance change already exists for this workflow run.")

        assert project.id == project_id and run.id == workflow_run_id
        change = MaintenanceChange(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            title=title.strip(),
            original_change_request=original_change_request.strip(),
            status=ProjectMaintenanceStatus.CHANGE_REQUESTED.value,
            revision_plan_document_id=None,
            metadata_=normalized_metadata,
            affected_items=[],
        )
        self.session.add(change)
        await self._commit()
        return change

    async def replace_affected_items(
        self,
        *,
        project_id: UUID,
        change_id: UUID,
        expected_updated_at: datetime,
        affected_items: tuple[MaintenanceAffectedItemCreate, ...],
    ) -> MaintenanceChange:
        _validate_expected_updated_at(expected_updated_at)
        _validate_affected_items(affected_items)
        change, run = await self._locked_change_context(
            project_id=project_id, change_id=change_id, allow_terminal=False
        )
        status = ProjectMaintenanceStatus(change.status)
        if status not in _AFFECTED_ITEM_EDIT_STATUSES:
            raise WorkflowStateError("Affected items cannot be changed in this workflow phase.")
        if change.updated_at != expected_updated_at:
            raise ConflictError("The maintenance change has been updated.")
        await self._validate_reference_scope(
            project_id=project_id,
            revision_plan_document_id=None,
            affected_items=affected_items,
        )
        change.affected_items.clear()
        try:
            await self.session.flush()
        except BaseException:
            await self.session.rollback()
            raise
        change.affected_items = [
            MaintenanceAffectedItem(
                position=position,
                item_type=item.item_type.value,
                stable_reference=item.stable_reference.strip(),
                impact_level=item.impact_level.value,
                reason=item.reason.strip(),
                existing_document_id=item.existing_document_id,
                existing_chapter_id=item.existing_chapter_id,
            )
            for position, item in enumerate(affected_items)
        ]
        change.updated_at = datetime.now(UTC)
        assert run.status == change.status
        await self._commit()
        return change

    async def get_change(self, *, project_id: UUID, change_id: UUID) -> MaintenanceChange:
        change = await self.session.scalar(
            select(MaintenanceChange)
            .options(selectinload(MaintenanceChange.affected_items))
            .where(MaintenanceChange.id == change_id, MaintenanceChange.project_id == project_id)
        )
        if change is None:
            raise NotFoundError("Maintenance change not found.")
        return change

    async def list_changes(self, *, project_id: UUID) -> list[MaintenanceChange]:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found.")
        return list(
            await self.session.scalars(
                select(MaintenanceChange)
                .options(selectinload(MaintenanceChange.affected_items))
                .where(MaintenanceChange.project_id == project_id)
                .order_by(MaintenanceChange.created_at, MaintenanceChange.id)
            )
        )

    async def update_change(
        self,
        *,
        project_id: UUID,
        change_id: UUID,
        expected_updated_at: datetime,
        status: ProjectMaintenanceStatus,
        revision_plan_document_id: UUID | None,
        applied_at: datetime | None,
        metadata: object,
    ) -> MaintenanceChange:
        if not isinstance(status, ProjectMaintenanceStatus):
            raise MaintenanceChangeValidationError("Maintenance status is not typed.")
        _validate_expected_updated_at(expected_updated_at)
        if applied_at is not None and (
            not isinstance(applied_at, datetime) or applied_at.tzinfo is None
        ):
            raise MaintenanceChangeValidationError("Applied time must be timezone-aware.")
        normalized_metadata = self.normalize_metadata(metadata)
        change, run = await self._locked_change_context(
            project_id=project_id,
            change_id=change_id,
            expected_run_status=status,
            allow_terminal=status in _TERMINAL_STATUSES,
        )
        if change.updated_at != expected_updated_at:
            raise ConflictError("The maintenance change has been updated.")
        try:
            current_status = ProjectMaintenanceStatus(change.status)
        except ValueError:
            raise WorkflowStateError("Maintenance change status is invalid.") from None
        _validate_lifecycle(
            current_status=current_status,
            target_status=status,
            current_revision_plan_document_id=change.revision_plan_document_id,
            target_revision_plan_document_id=revision_plan_document_id,
            current_applied_at=change.applied_at,
            target_applied_at=applied_at,
            has_affected_items=bool(change.affected_items),
        )
        await self._validate_reference_scope(
            project_id=project_id,
            revision_plan_document_id=revision_plan_document_id,
            affected_items=(),
        )
        change.status = status.value
        change.revision_plan_document_id = revision_plan_document_id
        change.applied_at = applied_at
        change.metadata_ = normalized_metadata
        change.updated_at = datetime.now(UTC)
        assert run.status == status.value
        await self._commit()
        return change

    async def _locked_project_run(
        self,
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        expected_status: ProjectMaintenanceStatus,
        allow_terminal: bool,
    ) -> tuple[Project, WorkflowRun]:
        project = await self.session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found.")
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id.is_(None),
                WorkflowRun.workflow_type == WorkflowType.PROJECT_MAINTENANCE.value,
            )
            .with_for_update()
        )
        if run is None:
            raise WorkflowStateError("Maintenance workflow run is not owned by the project.")
        self._validate_live_run(
            project=project,
            run=run,
            expected_status=expected_status,
            allow_terminal=allow_terminal,
        )
        return project, run

    async def _locked_change_context(
        self,
        *,
        project_id: UUID,
        change_id: UUID,
        expected_run_status: ProjectMaintenanceStatus | None = None,
        allow_terminal: bool,
    ) -> tuple[MaintenanceChange, WorkflowRun]:
        project = await self.session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found.")
        workflow_run_id = await self.session.scalar(
            select(MaintenanceChange.workflow_run_id).where(
                MaintenanceChange.id == change_id,
                MaintenanceChange.project_id == project_id,
            )
        )
        if workflow_run_id is None:
            raise NotFoundError("Maintenance change not found.")
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id.is_(None),
                WorkflowRun.workflow_type == WorkflowType.PROJECT_MAINTENANCE.value,
            )
            .with_for_update()
        )
        if run is None:
            raise WorkflowStateError("Maintenance workflow run is invalid.")
        change = await self.session.scalar(
            select(MaintenanceChange)
            .options(selectinload(MaintenanceChange.affected_items))
            .where(
                MaintenanceChange.id == change_id,
                MaintenanceChange.project_id == project_id,
                MaintenanceChange.workflow_run_id == run.id,
            )
            .with_for_update()
        )
        if change is None:
            raise NotFoundError("Maintenance change not found.")
        try:
            change_status = ProjectMaintenanceStatus(change.status)
        except ValueError:
            raise WorkflowStateError("Maintenance change status is invalid.") from None
        self._validate_live_run(
            project=project,
            run=run,
            expected_status=expected_run_status or change_status,
            allow_terminal=allow_terminal,
        )
        return change, run

    @staticmethod
    def _validate_live_run(
        *,
        project: Project,
        run: WorkflowRun,
        expected_status: ProjectMaintenanceStatus,
        allow_terminal: bool,
    ) -> None:
        is_terminal = expected_status in _TERMINAL_STATUSES
        if (
            project.current_workflow_id != run.id
            or run.status != expected_status.value
            or run.current_node != _NODES_BY_STATUS[expected_status]
            or run.next_node is not None
            or run.awaiting_user
            is not (expected_status is ProjectMaintenanceStatus.USER_CONFIRMATION)
            or (run.completed_at is not None) is not is_terminal
            or (is_terminal and not allow_terminal)
        ):
            raise WorkflowStateError("Maintenance workflow run is stale or inconsistent.")

    async def _validate_reference_scope(
        self,
        *,
        project_id: UUID,
        revision_plan_document_id: UUID | None,
        affected_items: tuple[MaintenanceAffectedItemCreate, ...],
    ) -> None:
        document_ids = {
            item.existing_document_id
            for item in affected_items
            if item.existing_document_id is not None
        }
        if revision_plan_document_id is not None:
            document_ids.add(revision_plan_document_id)
        chapter_ids = {
            item.existing_chapter_id
            for item in affected_items
            if item.existing_chapter_id is not None
        }
        if document_ids:
            owned_documents = set(
                await self.session.scalars(
                    select(Document.id).where(
                        Document.project_id == project_id, Document.id.in_(document_ids)
                    )
                )
            )
            if owned_documents != document_ids:
                raise NotFoundError("Maintenance document reference not found.")
        if chapter_ids:
            owned_chapters = set(
                await self.session.scalars(
                    select(Chapter.id).where(
                        Chapter.project_id == project_id, Chapter.id.in_(chapter_ids)
                    )
                )
            )
            if owned_chapters != chapter_ids:
                raise NotFoundError("Maintenance chapter reference not found.")

    @staticmethod
    def normalize_metadata(metadata: object) -> dict:
        if type(metadata) is not dict:
            raise MaintenanceChangeValidationError("Metadata must be a JSON object.")
        _validate_json_structure(metadata)
        try:
            encoded = json.dumps(
                metadata,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError, OverflowError):
            raise MaintenanceChangeValidationError("Metadata must contain JSON values.") from None
        if len(encoded) > _MAX_METADATA_BYTES:
            raise MaintenanceChangeValidationError("Metadata is too large.")
        return json.loads(encoded)

    async def _commit(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError("The maintenance change conflicts with persisted data.") from None
        except BaseException:
            await self.session.rollback()
            raise
        try:
            await self.session.commit()
        except IntegrityError:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise ConflictError("The maintenance change conflicts with persisted data.") from None
        except BaseException:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise MaintenanceChangeCommitIndeterminateError() from None


def _require_text(
    value: object, label: str, *, max_length: int, allow_multiline: bool
) -> None:
    if type(value) is not str or not value.strip():
        raise MaintenanceChangeValidationError(f"{label} is required.")
    if len(value) > max_length:
        raise MaintenanceChangeValidationError(f"{label} is too long.")
    allowed = {"\t", "\n", "\r"} if allow_multiline else set()
    if any(
        unicodedata.category(character) == "Cc" and character not in allowed
        for character in value
    ):
        raise MaintenanceChangeValidationError(f"{label} contains control characters.")


def _validate_expected_updated_at(value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MaintenanceChangeValidationError("Expected update time must be timezone-aware.")


def _validate_affected_items(
    affected_items: tuple[MaintenanceAffectedItemCreate, ...],
) -> None:
    if type(affected_items) is not tuple:
        raise MaintenanceChangeValidationError("Affected items must be an immutable tuple.")
    if len(affected_items) > _MAX_AFFECTED_ITEMS:
        raise MaintenanceChangeValidationError("Too many affected items.")
    if any(not isinstance(item, MaintenanceAffectedItemCreate) for item in affected_items):
        raise MaintenanceChangeValidationError("Affected items are not typed.")
    identities = {(item.item_type, item.stable_reference.strip()) for item in affected_items}
    if len(identities) != len(affected_items):
        raise MaintenanceChangeValidationError("Affected-item references must be unique.")


def _validate_lifecycle(
    *,
    current_status: ProjectMaintenanceStatus | None,
    target_status: ProjectMaintenanceStatus,
    current_revision_plan_document_id: UUID | None,
    target_revision_plan_document_id: UUID | None,
    current_applied_at: datetime | None,
    target_applied_at: datetime | None,
    has_affected_items: bool,
) -> None:
    if current_status is None:
        if (
            target_status is not ProjectMaintenanceStatus.CHANGE_REQUESTED
            or target_revision_plan_document_id is not None
            or target_applied_at is not None
            or has_affected_items
        ):
            raise MaintenanceChangeValidationError(
                "A maintenance change must start as a clean change request."
            )
        return
    if (current_status, target_status) not in _ALLOWED_TRANSITIONS:
        raise WorkflowStateError("Maintenance status transition is not allowed.")

    early_statuses = {
        ProjectMaintenanceStatus.CHANGE_REQUESTED,
        ProjectMaintenanceStatus.LORE_IMPACT_ANALYSIS,
        ProjectMaintenanceStatus.CHIEF_EDITOR_IMPACT_ANALYSIS,
    }
    plan_required_statuses = {
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        ProjectMaintenanceStatus.APPLY_CHANGE,
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        ProjectMaintenanceStatus.PROJECT_UPDATED,
        ProjectMaintenanceStatus.CANCELLED,
    }
    if target_status in early_statuses and target_revision_plan_document_id is not None:
        raise MaintenanceChangeValidationError("This workflow phase cannot have a revision plan.")
    if target_status in plan_required_statuses and target_revision_plan_document_id is None:
        raise MaintenanceChangeValidationError("This workflow phase requires a revision plan.")
    if target_status in {
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
        ProjectMaintenanceStatus.PROJECT_UPDATED,
    } and target_applied_at is None:
        raise MaintenanceChangeValidationError("This workflow phase requires an applied time.")
    if target_status in early_statuses | {ProjectMaintenanceStatus.CANCELLED} and (
        target_applied_at is not None
    ):
        raise MaintenanceChangeValidationError(
            "This maintenance status cannot have an applied time."
        )

    transition = (current_status, target_status)
    if transition == (
        ProjectMaintenanceStatus.REVISION_PLAN,
        ProjectMaintenanceStatus.REVISION_PLAN,
    ):
        pass
    elif target_status is ProjectMaintenanceStatus.REVISION_PLAN and current_status in {
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        ProjectMaintenanceStatus.CONSISTENCY_REVIEW,
    }:
        if target_revision_plan_document_id is not None:
            raise MaintenanceChangeValidationError(
                "Corrective revision must clear the previous revision plan."
            )
    elif target_revision_plan_document_id != current_revision_plan_document_id:
        raise MaintenanceChangeValidationError(
            "Revision-plan reference cannot change in this transition."
        )

    if transition == (
        ProjectMaintenanceStatus.APPLY_CHANGE,
        ProjectMaintenanceStatus.APPLY_CHANGE,
    ):
        if current_applied_at is not None and target_applied_at != current_applied_at:
            raise MaintenanceChangeValidationError("Applied time is immutable once recorded.")
    elif target_applied_at != current_applied_at:
        raise MaintenanceChangeValidationError("Applied time cannot change in this transition.")

    if transition == (
        ProjectMaintenanceStatus.USER_CONFIRMATION,
        ProjectMaintenanceStatus.APPLY_CHANGE,
    ) and current_applied_at is not None:
        raise WorkflowStateError("A consistency-warning confirmation cannot enter apply-change.")


def _validate_json_structure(root: dict) -> None:
    stack: list[tuple[object, int, frozenset[int]]] = [(root, 1, frozenset())]
    nodes = 0
    while stack:
        value, depth, ancestors = stack.pop()
        nodes += 1
        if nodes > _MAX_METADATA_NODES:
            raise MaintenanceChangeValidationError("Metadata contains too many values.")
        if not isinstance(value, (dict, list)):
            continue
        if depth > _MAX_METADATA_DEPTH:
            raise MaintenanceChangeValidationError("Metadata is too deeply nested.")
        identity = id(value)
        if identity in ancestors:
            raise MaintenanceChangeValidationError("Metadata contains a cycle.")
        next_ancestors = ancestors | {identity}
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise MaintenanceChangeValidationError("Metadata keys must be strings.")
            children = value.values()
        else:
            children = value
        stack.extend((child, depth + 1, next_ancestors) for child in children)
