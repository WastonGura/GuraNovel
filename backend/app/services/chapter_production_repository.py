"""Scoped, session-bound persistence primitives for Chapter Production V2."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Chapter,
    Document,
    DocumentType,
    DocumentVersion,
    Project,
    WorkflowRun,
    WorkflowType,
)


class _ChapterProductionRepositoryError(Exception):
    """Content-free base for repository invariant failures."""


class _ChapterProductionRepositoryValidationError(_ChapterProductionRepositoryError):
    """A fixed scoped-lookup or cardinality failure."""

    def __init__(self) -> None:
        super().__init__("Chapter production repository lookup failed.")


class _ChapterProductionRepositoryReconciliationError(_ChapterProductionRepositoryError):
    """A fixed durable-evidence failure that requires reconciliation."""

    def __init__(self) -> None:
        super().__init__("Chapter production repository requires reconciliation.")


def _validation_error() -> _ChapterProductionRepositoryValidationError:
    return _ChapterProductionRepositoryValidationError()


def _reconciliation_error() -> _ChapterProductionRepositoryReconciliationError:
    return _ChapterProductionRepositoryReconciliationError()


def _validated_ids(*values: UUID) -> tuple[UUID, ...]:
    try:
        selected = tuple(UUID(str(value)) for value in values)
    except (AttributeError, TypeError, ValueError):
        selected = ()
    if any(
        isinstance(value, (str, bytes))
        or not hasattr(value, "int")
        or len(selected) != len(values)
        or selected[index].int == 0
        for index, value in enumerate(values)
    ):
        raise _validation_error()
    return selected


def _validated_reconciliation_ids(*values: UUID) -> tuple[UUID, ...]:
    try:
        selected = tuple(UUID(str(value)) for value in values)
    except (AttributeError, TypeError, ValueError):
        selected = ()
    if any(
        isinstance(value, (str, bytes))
        or not hasattr(value, "int")
        or len(selected) != len(values)
        or selected[index].int == 0
        for index, value in enumerate(values)
    ):
        raise _reconciliation_error()
    return selected


def _valid_operation_key(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class ChapterProductionRepository:
    """Short-lived authoritative reads and locks within a caller-owned transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        contract_version: str,
        inactive_run_statuses: AbstractSet[str],
    ) -> None:
        if (
            type(contract_version) is not str
            or not contract_version
            or "\x00" in contract_version
            or not isinstance(inactive_run_statuses, AbstractSet)
            or any(type(status) is not str or not status for status in inactive_run_statuses)
        ):
            raise _validation_error() from None
        self.session = session
        self.contract_version = contract_version
        self.inactive_run_statuses = frozenset(inactive_run_statuses)

    async def require_project_owner(
        self, project_id: UUID, actor_user_id: UUID, *, lock: bool = True
    ) -> None:
        project_id, actor_user_id = _validated_ids(project_id, actor_user_id)
        statement = (
            select(Project.id)
            .where(Project.id == project_id, Project.owner_id == actor_user_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        if await self.session.scalar(statement) is None:
            raise _validation_error() from None

    async def approved_outline(
        self, project_id: UUID, chapter_id: UUID, *, lock: bool
    ) -> tuple[Chapter, Document, DocumentVersion]:
        chapter = await self.chapter(project_id, chapter_id, lock=lock)
        outline, version = await self._outline_for_fresh_chapter(
            chapter, project_id, lock=lock
        )
        return chapter, outline, version

    async def outline_for_chapter(
        self, project_id: UUID, chapter_id: UUID, *, lock: bool
    ) -> tuple[Document, DocumentVersion]:
        chapter = await self.chapter(project_id, chapter_id, lock=lock)
        return await self._outline_for_fresh_chapter(chapter, project_id, lock=lock)

    async def _outline_for_fresh_chapter(
        self, chapter: Chapter, project_id: UUID, *, lock: bool
    ) -> tuple[Document, DocumentVersion]:
        if (
            chapter.project_id != project_id
            or chapter.status != "OUTLINE_APPROVED"
            or chapter.current_outline_document_id is None
        ):
            raise _validation_error() from None
        statement = (
            select(Document)
            .options(selectinload(Document.project))
            .where(
                Document.id == chapter.current_outline_document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter.id,
                Document.type.in_(
                    (
                        DocumentType.CHAPTER_SELECTED_OUTLINE.value,
                        DocumentType.CHAPTER_OUTLINE_OPTIONS.value,
                    )
                ),
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        outline = await self.session.scalar(statement)
        if outline is None or outline.current_version_id is None:
            raise _validation_error() from None
        version_statement = (
            select(DocumentVersion)
            .where(
                DocumentVersion.id == outline.current_version_id,
                DocumentVersion.document_id == outline.id,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            version_statement = version_statement.with_for_update()
        version = await self.session.scalar(version_statement)
        if version is None:
            raise _validation_error() from None
        return outline, version

    async def chapter(self, project_id: UUID, chapter_id: UUID, *, lock: bool) -> Chapter:
        project_id, chapter_id = _validated_ids(project_id, chapter_id)
        if lock:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"chapter-production-v2:{chapter_id}"},
            )
        statement = (
            select(Chapter)
            .where(Chapter.id == chapter_id, Chapter.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        chapter = await self.session.scalar(statement)
        if chapter is None:
            raise _validation_error() from None
        return chapter

    async def run(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        lock: bool,
    ) -> WorkflowRun:
        project_id, chapter_id, workflow_run_id = _validated_ids(
            project_id, chapter_id, workflow_run_id
        )
        statement = (
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id == chapter_id,
                WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        run = await self.session.scalar(statement)
        if run is None or not self._is_current_contract_run(run):
            raise _validation_error() from None
        return run

    async def operation_run(
        self, project_id: UUID, chapter_id: UUID, operation_key: str
    ) -> WorkflowRun | None:
        """Perform operation identity classification without validating full run metadata."""

        project_id, chapter_id = _validated_ids(project_id, chapter_id)
        if not _valid_operation_key(operation_key):
            raise _validation_error()
        runs = list(
            await self.session.scalars(
                select(WorkflowRun)
                .where(
                    WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
                    or_(
                        WorkflowRun.chapter_id == chapter_id,
                        WorkflowRun.metadata_.contains(
                            {
                                "contract_version": self.contract_version,
                                "operation_key": operation_key,
                            }
                        ),
                    ),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        current_contract_runs: list[tuple[WorkflowRun, str]] = []
        for run in runs:
            metadata = run.metadata_
            if type(metadata) is not dict:
                raise _validation_error() from None
            if metadata.get("contract_version") != self.contract_version:
                continue
            stored_operation_key = metadata.get("operation_key")
            if (
                run.project_id != project_id
                or run.chapter_id != chapter_id
                or not _valid_operation_key(stored_operation_key)
            ):
                raise _validation_error()
            current_contract_runs.append((run, stored_operation_key))

        matches = [run for run, key in current_contract_runs if key == operation_key]
        if len(matches) > 1:
            raise _validation_error() from None
        if any(
            key != operation_key and run.status not in self.inactive_run_statuses
            for run, key in current_contract_runs
        ):
            raise _validation_error() from None
        return matches[0] if matches else None

    async def locked_current_document_version(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        expected_document_type: DocumentType,
    ) -> DocumentVersion:
        project_id, chapter_id, document_id = _validated_reconciliation_ids(
            project_id, chapter_id, document_id
        )
        if type(expected_document_type) is not DocumentType:
            raise _reconciliation_error()
        locked_document = await self.session.scalar(
            select(Document)
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type == expected_document_type.value,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_document is None or locked_document.current_version_id is None:
            raise _reconciliation_error()
        version = await self.session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == locked_document.current_version_id,
                DocumentVersion.document_id == locked_document.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version is None:
            raise _reconciliation_error()
        return version

    def _is_current_contract_run(self, run: WorkflowRun) -> bool:
        metadata = run.metadata_
        return type(metadata) is dict and metadata.get("contract_version") == self.contract_version


__all__ = ["ChapterProductionRepository"]
