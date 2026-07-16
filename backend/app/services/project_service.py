"""Transactional project creation and retrieval."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Project
from app.workspace import ProjectWorkspace


class ProjectCommitIndeterminateError(AppError):
    """Raised when project commit acknowledgement is lost after workspace allocation."""

    status_code = 500
    code = "project_commit_indeterminate"
    default_message = "The project creation outcome could not be confirmed. Reconciliation is required before retrying."


class ProjectWorkspaceCleanupError(RuntimeError):
    """Raised when a failed pre-commit operation leaves a workspace unsafe to ignore."""


class ProjectService:
    """Application boundary for projects and their service-owned workspaces."""

    def __init__(self, session: AsyncSession, workspace: ProjectWorkspace | None = None) -> None:
        self.session = session
        self.workspace = workspace

    async def create_project(
        self,
        *,
        slug: str,
        title: str,
        genre: str | None = None,
        target_platform: str | None = None,
        metadata: dict | None = None,
    ) -> Project:
        assert self.workspace is not None
        await self._lock_slug(slug)
        existing = await self.session.scalar(select(Project.id).where(Project.slug == slug))
        if existing is not None:
            raise ConflictError("A project with this slug already exists.")

        root = self.workspace.root_for(slug)
        allocated_here = not (root.exists() or root.is_symlink())
        try:
            workspace_root = self.workspace.create(slug)
            project = Project(
                slug=slug,
                title=title,
                genre=genre,
                target_platform=target_platform,
                metadata_=metadata or {},
                workspace_root=str(workspace_root),
            )
            self.session.add(project)
            await self.session.flush()
        except BaseException as precommit_error:
            rollback_error: BaseException | None = None
            try:
                await self.session.rollback()
            except BaseException as error:
                rollback_error = error

            if allocated_here:
                try:
                    removed = self.workspace.remove_new_empty(slug)
                    if not removed and (root.exists() or root.is_symlink()):
                        raise ProjectWorkspaceCleanupError(
                            "Failed to remove a workspace allocated for an uncommitted project."
                        )
                except BaseException as cleanup_error:
                    if rollback_error is not None:
                        cleanup_error.add_note(f"Database rollback also failed: {rollback_error!r}")
                    raise ProjectWorkspaceCleanupError(
                        "Workspace compensation failed after a pre-commit error."
                    ) from cleanup_error

            if rollback_error is not None:
                raise precommit_error from rollback_error
            raise

        try:
            await self.session.commit()
        except BaseException as error:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise ProjectCommitIndeterminateError() from error
        return project

    async def get_project(self, project_id: UUID) -> Project:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        return project

    async def list_projects(self) -> list[Project]:
        return list(await self.session.scalars(select(Project).order_by(Project.created_at, Project.id)))

    async def _lock_slug(self, slug: str) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"project:{slug}"},
        )
