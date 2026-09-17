"""Transactional setting collection lifecycle, permissions, and document management."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models import (
    Document,
    DocumentSource,
    DocumentType,
    Project,
    SettingCollection,
)
from app.agents.setting_proposal_contracts import SettingChangeProposal
from app.services.document_service import (
    SETTING_COLLECTION_DOCUMENT_TYPES,
    DocumentService,
)
from app.workspace import ProjectWorkspace


class SettingCollectionCommitIndeterminateError(AppError):
    """Raised when setting collection commit outcome cannot be confirmed."""

    status_code = 500
    code = "setting_collection_commit_indeterminate"
    default_message = (
        "The setting collection creation outcome could not be confirmed. "
        "Reconciliation is required before retrying."
    )


class SettingCollectionWorkspaceCleanupError(RuntimeError):
    """Raised when workspace compensation fails after a pre-commit error."""


class SettingCollectionService:
    """Application boundary for setting collections, novel bindings, and setting documents."""

    def __init__(
        self, session: AsyncSession, workspace: ProjectWorkspace | None = None
    ) -> None:
        self.session = session
        self.workspace = workspace

    async def create_setting_collection(
        self,
        *,
        title: str,
        slug: str | None = None,
        description: str | None = None,
        owner_id: UUID | None = None,
        metadata: dict | None = None,
    ) -> SettingCollection:
        if not slug:
            import re
            from uuid import uuid4

            clean_title = re.sub(r"[^a-zA-Z0-9_-]", "-", title.lower()).strip("-")
            slug = f"{clean_title[:30]}-{uuid4().hex[:6]}" if clean_title else f"col-{uuid4().hex[:8]}"

        await self._lock_slug(slug)
        existing = await self.session.scalar(
            select(SettingCollection.id).where(SettingCollection.slug == slug)
        )
        if existing is not None:
            raise ConflictError("A setting collection with this slug already exists.")

        workspace_root: str
        allocated_here = False
        if self.workspace is not None:
            root = self.workspace.root_for(slug)
            allocated_here = not (root.exists() or root.is_symlink())
            try:
                workspace_root = str(self.workspace.create(slug))
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
                            raise SettingCollectionWorkspaceCleanupError(
                                "Failed to remove a workspace allocated for an uncommitted setting collection."
                            )
                    except BaseException as cleanup_error:
                        if rollback_error is not None:
                            cleanup_error.add_note(
                                f"Database rollback also failed: {rollback_error!r}"
                            )
                        raise SettingCollectionWorkspaceCleanupError(
                            "Workspace compensation failed after a pre-commit error."
                        ) from cleanup_error

                if rollback_error is not None:
                    raise precommit_error from rollback_error
                raise
        else:
            workspace_root = f"/tmp/setting_collections/{slug}"

        try:
            collection = SettingCollection(
                slug=slug,
                title=title,
                description=description,
                owner_id=owner_id,
                metadata_=metadata or {},
                workspace_root=workspace_root,
                status="active",
                revision=1,
            )
            self.session.add(collection)
            await self.session.flush()
        except BaseException as precommit_error:
            rollback_error = None
            try:
                await self.session.rollback()
            except BaseException as error:
                rollback_error = error

            if allocated_here and self.workspace is not None:
                try:
                    root = self.workspace.root_for(slug)
                    removed = self.workspace.remove_new_empty(slug)
                    if not removed and (root.exists() or root.is_symlink()):
                        raise SettingCollectionWorkspaceCleanupError(
                            "Failed to remove a workspace allocated for an uncommitted setting collection."
                        )
                except BaseException as cleanup_error:
                    if rollback_error is not None:
                        cleanup_error.add_note(
                            f"Database rollback also failed: {rollback_error!r}"
                        )
                    raise SettingCollectionWorkspaceCleanupError(
                        "Workspace compensation failed after a pre-commit error."
                    ) from cleanup_error

            if rollback_error is not None:
                raise precommit_error from rollback_error
            raise

        try:
            await self.session.commit()
            await self.session.refresh(collection)
        except BaseException as error:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            raise SettingCollectionCommitIndeterminateError() from error

        return collection

    async def get_setting_collection(
        self, collection_id: UUID, *, actor_user_id: UUID | None = None
    ) -> SettingCollection:
        collection = await self.session.get(SettingCollection, collection_id)
        if collection is None:
            raise NotFoundError("Setting collection not found.")
        if (
            actor_user_id is not None
            and collection.owner_id is not None
            and collection.owner_id != actor_user_id
        ):
            raise ForbiddenError("You do not have permission to access this setting collection.")
        return collection

    async def update_setting_collection(
        self,
        collection_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
        actor_user_id: UUID | None = None,
    ) -> SettingCollection:
        collection = await self.get_setting_collection(
            collection_id, actor_user_id=actor_user_id
        )
        if collection.status == "archived":
            raise ConflictError("Archived setting collection cannot be modified.")

        if title is not None:
            collection.title = title
        if description is not None:
            collection.description = description
        if metadata is not None:
            current_metadata = dict(collection.metadata_ or {})
            current_metadata.update(metadata)
            collection.metadata_ = current_metadata

        await self.session.commit()
        await self.session.refresh(collection)
        return collection

    async def archive_setting_collection(
        self, collection_id: UUID, *, actor_user_id: UUID | None = None
    ) -> SettingCollection:
        collection = await self.get_setting_collection(
            collection_id, actor_user_id=actor_user_id
        )
        collection.status = "archived"
        await self.session.commit()
        await self.session.refresh(collection)
        return collection

    async def list_setting_collections(
        self, *, owner_id: UUID | None = None, status: str | None = None
    ) -> list[SettingCollection]:
        stmt = select(SettingCollection).order_by(
            SettingCollection.created_at, SettingCollection.id
        )
        if owner_id is not None:
            stmt = stmt.where(
                or_(
                    SettingCollection.owner_id == owner_id,
                    SettingCollection.owner_id.is_(None),
                )
            )
        if status is not None:
            stmt = stmt.where(SettingCollection.status == status)
        return list(await self.session.scalars(stmt))

    async def list_projects_for_collection(
        self, collection_id: UUID, *, actor_user_id: UUID | None = None
    ) -> list[Project]:
        await self.get_setting_collection(collection_id, actor_user_id=actor_user_id)
        stmt = (
            select(Project)
            .where(Project.setting_collection_id == collection_id)
            .order_by(Project.created_at, Project.id)
        )
        return list(await self.session.scalars(stmt))

    async def list_documents_for_collection(
        self, collection_id: UUID, *, actor_user_id: UUID | None = None
    ) -> list[Document]:
        await self.get_setting_collection(collection_id, actor_user_id=actor_user_id)
        stmt = (
            select(Document)
            .options(selectinload(Document.current_version))
            .where(Document.setting_collection_id == collection_id)
            .order_by(Document.created_at, Document.id)
        )
        return list(await self.session.scalars(stmt))

    async def create_document_for_collection(
        self,
        collection_id: UUID,
        *,
        document_type: DocumentType,
        title: str | None,
        path: str,
        content: str,
        source: DocumentSource = DocumentSource.USER,
        actor_user_id: UUID | None = None,
        agent_role: str | None = None,
        workflow_run_id: UUID | None = None,
        change_summary: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Document:
        collection = await self.get_setting_collection(
            collection_id, actor_user_id=actor_user_id
        )
        if collection.status == "archived":
            raise ConflictError("Archived setting collection cannot be modified.")
        if document_type not in SETTING_COLLECTION_DOCUMENT_TYPES:
            raise ConflictError(
                f"Document type '{document_type.value}' is not permitted for setting collections."
            )

        doc_service = DocumentService(self.session)
        document = await doc_service.create_document(
            setting_collection_id=collection_id,
            document_type=document_type,
            title=title,
            path=path,
            content=content,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary,
            metadata=metadata,
        )
        return document

    async def apply_setting_change_proposal(
        self,
        collection_id: UUID,
        proposal: SettingChangeProposal,
        *,
        actor_user_id: UUID | None = None,
    ) -> Document:
        if proposal.setting_collection_id != collection_id:
            raise ConflictError("Proposal setting collection does not match target collection.")

        collection = await self.get_setting_collection(
            collection_id, actor_user_id=actor_user_id
        )
        if collection.status == "archived":
            raise ConflictError("Archived setting collection cannot be modified.")

        doc_service = DocumentService(self.session)
        source = DocumentSource.USER
        if proposal.source_task.agent_role:
            try:
                source = DocumentSource(proposal.source_task.agent_role)
            except ValueError:
                source = DocumentSource.USER

        if proposal.target_document_id is not None:
            document = await self.session.get(Document, proposal.target_document_id)
            if document is None or document.setting_collection_id != collection_id:
                raise NotFoundError("Target setting document not found in collection.")

            await doc_service.write_document(
                document_id=proposal.target_document_id,
                content=proposal.proposed_content,
                source=source,
                expected_current_version_id=proposal.base_version_id,
                actor_user_id=actor_user_id,
                agent_role=proposal.source_task.agent_role,
                workflow_run_id=proposal.source_task.workflow_run_id,
                change_summary=proposal.reason,
            )
            return await self.session.scalar(
                select(Document)
                .options(
                    selectinload(Document.current_version),
                    selectinload(Document.project),
                    selectinload(Document.setting_collection),
                )
                .where(Document.id == proposal.target_document_id)
            )
        else:
            import re
            from uuid import uuid4

            doc_type = (
                DocumentType.WORLD_OVERVIEW
                if proposal.category.lower() in ("world", "world_overview")
                else DocumentType.CHARACTER_PROFILE
            )
            clean_title = re.sub(r"[^a-zA-Z0-9_-]", "-", proposal.title.lower()).strip("-")
            slug = f"{clean_title[:30]}-{uuid4().hex[:6]}" if clean_title else f"note-{uuid4().hex[:8]}"
            path = f"{proposal.category}/{slug}.md"

            document = await doc_service.create_document(
                setting_collection_id=collection_id,
                document_type=doc_type,
                title=proposal.title,
                path=path,
                content=proposal.proposed_content,
                source=source,
                actor_user_id=actor_user_id,
                agent_role=proposal.source_task.agent_role,
                workflow_run_id=proposal.source_task.workflow_run_id,
                change_summary=proposal.reason,
                metadata={"category": proposal.category},
            )
            return await self.session.scalar(
                select(Document)
                .options(
                    selectinload(Document.current_version),
                    selectinload(Document.project),
                    selectinload(Document.setting_collection),
                )
                .where(Document.id == document.id)
            )

    async def _lock_slug(self, slug: str) -> None:
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"
            ),
            {"lock_key": f"setting_collection:{slug}"},
        )
