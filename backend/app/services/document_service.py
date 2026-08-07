"""Transactional, versioned Markdown document persistence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import log_event
from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MAX_CHAPTER_CONTENT_BYTES,
    ChapterSegmentMap,
    derive_chapter_segment_map,
    normalize_chapter_content,
    validate_segment_map_evidence_integrity,
)
from app.models import Chapter, Document, DocumentSource, DocumentType, DocumentVersion, Project
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path, workspace_path_parts
from app.workspace.word_count import count_words


class DocumentVersionConflictError(ConflictError):
    """Raised when a write is based on a no-longer-current document version."""

    code = "document_version_conflict"


class ReservedDocumentPathError(ConflictError):
    """Raised when a document path would target version snapshots."""

    code = "reserved_document_path"
    default_message = "Document paths cannot use the reserved .versions namespace."


class DocumentCommitIndeterminateError(AppError):
    """Raised when a database commit may have succeeded but cannot be confirmed."""

    status_code = 500
    code = "document_commit_indeterminate"
    default_message = "The document save outcome could not be confirmed. Reconciliation is required before retrying."


class ChapterSegmentSnapshotMismatchError(AppError):
    """Raised when an immutable snapshot no longer matches its database identity."""

    status_code = 500
    code = "chapter_segment_snapshot_mismatch"
    default_message = "The chapter snapshot could not be verified."


class DocumentVersionMetadataError(AppError):
    """Raised when a caller tries to persist non-mechanical version metadata."""

    code = "document_version_metadata_invalid"
    default_message = "Document version metadata is invalid."


_CONTRACT_VERSION_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_OPERATION_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")


def _is_canonical_nonzero_uuid(value: str) -> bool:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return parsed.int != 0 and str(parsed) == value


@dataclass(frozen=True)
class _FileBackup:
    path: str
    content: str | None


@dataclass(frozen=True)
class CurrentDocumentContent:
    """An immutable current-version identifier and its snapshot content."""

    version_id: UUID
    content: str


class DocumentService:
    """The application boundary for creating, writing, and restoring documents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_document(
        self,
        *,
        project_id: UUID,
        document_type: DocumentType,
        title: str | None,
        path: str,
        content: str,
        source: DocumentSource,
        chapter_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        agent_role: str | None = None,
        workflow_run_id: UUID | None = None,
        change_summary: str | None = None,
        version_metadata: dict[str, object] | None = None,
    ) -> Document:
        version_metadata = self._validated_version_metadata(version_metadata)
        self._ensure_document_path_is_not_reserved(path)
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        await self._lock_create_path(project_id, path)
        existing = await self.session.scalar(
            select(Document.id).where(Document.project_id == project_id, Document.path == path)
        )
        if existing is not None:
            raise ConflictError("A document already exists at this path.")

        document = Document(
            project_id=project_id,
            chapter_id=chapter_id,
            type=document_type.value,
            title=title,
            path=path,
        )
        self.session.add(document)
        try:
            await self.session.flush()
        except BaseException:
            await self.session.rollback()
            raise

        version = self._new_version(
            document=document,
            version_number=1,
            parent_version_id=None,
            content=content,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary,
            version_metadata=version_metadata,
        )
        document.current_version = version
        self.session.add(version)
        await self._commit_with_file_writes(
            MarkdownStore(Path(project.workspace_root)),
            ((document.path, content), (version.snapshot_path, content)),
        )
        log_event("document_written", document_id=document.id, version_id=version.id)
        return document

    async def stage_create_document(
        self, **kwargs
    ) -> tuple[Document, tuple[str, str], tuple[str, str]]:
        """Stage a create in the caller's transaction; deliberately does not commit."""
        kwargs["version_metadata"] = self._validated_version_metadata(
            kwargs.get("version_metadata")
        )
        project_id = kwargs["project_id"]
        path = kwargs["path"]
        content = kwargs["content"]
        self._ensure_document_path_is_not_reserved(path)
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        await self._lock_create_path(project_id, path)
        if (
            await self.session.scalar(
                select(Document.id).where(Document.project_id == project_id, Document.path == path)
            )
            is not None
        ):
            raise ConflictError("A document already exists at this path.")
        document = Document(
            project_id=project_id,
            chapter_id=kwargs.get("chapter_id"),
            type=kwargs["document_type"].value,
            title=kwargs.get("title"),
            path=path,
        )
        document.project = project
        self.session.add(document)
        await self.session.flush()
        version = self._new_version(
            document=document,
            version_number=1,
            parent_version_id=None,
            content=content,
            source=kwargs["source"],
            actor_user_id=kwargs.get("actor_user_id"),
            agent_role=kwargs.get("agent_role"),
            workflow_run_id=kwargs.get("workflow_run_id"),
            change_summary=kwargs.get("change_summary"),
            version_metadata=kwargs.get("version_metadata"),
        )
        document.current_version = version
        self.session.add(version)
        await self.session.flush()
        return document, (document.path, content), (version.snapshot_path or "", content)

    async def stage_write_document(
        self, **kwargs
    ) -> tuple[DocumentVersion, tuple[str, str], tuple[str, str]]:
        """Stage a version in the caller's transaction; deliberately does not commit."""
        kwargs["version_metadata"] = self._validated_version_metadata(
            kwargs.get("version_metadata")
        )
        document = await self._locked_document(kwargs["document_id"])
        self._ensure_expected_current_version(document, kwargs["expected_current_version_id"])
        content = kwargs["content"]
        version = self._new_version(
            document=document,
            version_number=await self._next_version_number(document.id),
            parent_version_id=document.current_version_id,
            content=content,
            source=kwargs["source"],
            actor_user_id=kwargs.get("actor_user_id"),
            agent_role=kwargs.get("agent_role"),
            workflow_run_id=kwargs.get("workflow_run_id"),
            change_summary=kwargs.get("change_summary"),
            version_metadata=kwargs.get("version_metadata"),
        )
        document.current_version = version
        self.session.add(version)
        await self.session.flush()
        return version, (document.path, content), (version.snapshot_path or "", content)

    def write_staged_files(self, document: Document, writes: Sequence[tuple[str, str]]) -> None:
        """Called only after known DB commit; failures are explicit reconciliation cases."""
        try:
            store = self._store_for(document)
            for path, content in writes:
                store.write(path, content)
        except Exception:
            raise DocumentCommitIndeterminateError() from None

    async def write_document(
        self,
        *,
        document_id: UUID,
        content: str,
        source: DocumentSource,
        expected_current_version_id: UUID | None,
        actor_user_id: UUID | None = None,
        agent_role: str | None = None,
        workflow_run_id: UUID | None = None,
        change_summary: str | None = None,
        version_metadata: dict[str, object] | None = None,
    ) -> DocumentVersion:
        version_metadata = self._validated_version_metadata(version_metadata)
        document = await self._locked_document(document_id)
        self._ensure_expected_current_version(document, expected_current_version_id)
        version = self._new_version(
            document=document,
            version_number=await self._next_version_number(document.id),
            parent_version_id=document.current_version_id,
            content=content,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary,
            version_metadata=version_metadata,
        )
        document.current_version = version
        self.session.add(version)
        await self._commit_with_file_writes(
            self._store_for(document), ((document.path, content), (version.snapshot_path, content))
        )
        log_event("document_written", document_id=document.id, version_id=version.id)
        return version

    async def restore_document(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        source: DocumentSource,
        expected_current_version_id: UUID | None,
        actor_user_id: UUID | None = None,
        agent_role: str | None = None,
        workflow_run_id: UUID | None = None,
        change_summary: str | None = None,
        version_metadata: dict[str, object] | None = None,
    ) -> DocumentVersion:
        version_metadata = self._validated_version_metadata(version_metadata)
        document = await self._locked_document(document_id)
        self._ensure_expected_current_version(document, expected_current_version_id)
        target = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id, DocumentVersion.document_id == document.id
            )
        )
        if target is None:
            raise NotFoundError("Document version not found.")
        content = self._store_for(document).read(self._snapshot_path(target))
        restored = await self._append_version(
            document=document,
            content=content,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary,
            version_metadata=version_metadata,
        )
        log_event(
            "document_restored",
            document_id=document.id,
            version_id=restored.id,
            restored_from_version_id=version_id,
        )
        return restored

    async def read_current_content(self, document_id: UUID) -> CurrentDocumentContent:
        document = await self._document(document_id)
        version = document.current_version
        if version is None:
            raise NotFoundError("Document not found.")
        return CurrentDocumentContent(
            version_id=version.id,
            content=self._store_for(document).read(self._snapshot_path(version)),
        )

    async def read_version_content(self, document_id: UUID, version_id: UUID) -> str:
        document = await self._document(document_id)
        version = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id, DocumentVersion.document_id == document.id
            )
        )
        if version is None:
            raise NotFoundError("Document version not found.")
        return self._store_for(document).read(self._snapshot_path(version))

    async def derive_chapter_segment_map(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        version_id: UUID,
        segmenter_version: str = CURRENT_CHAPTER_SEGMENTER_VERSION,
    ) -> ChapterSegmentMap:
        """Derive locators from one exact, verified historical chapter snapshot."""

        return await self._derive_chapter_segment_map_for_types(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            segmenter_version=segmenter_version,
            document_types=(DocumentType.CHAPTER_DRAFT, DocumentType.CHAPTER_FINAL),
        )

    async def derive_chapter_production_segment_map(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        version_id: UUID,
        segmenter_version: str = CURRENT_CHAPTER_SEGMENTER_VERSION,
    ) -> ChapterSegmentMap:
        """Verify and derive a production-owned outline or draft snapshot."""

        return await self._derive_chapter_segment_map_for_types(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            segmenter_version=segmenter_version,
            document_types=(
                DocumentType.CHAPTER_SELECTED_OUTLINE,
                DocumentType.CHAPTER_OUTLINE_OPTIONS,
                DocumentType.CHAPTER_DRAFT,
            ),
        )

    async def _derive_chapter_segment_map_for_types(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        version_id: UUID,
        segmenter_version: str,
        document_types: Sequence[DocumentType],
    ) -> ChapterSegmentMap:
        try:
            project_id, chapter_id, document_id, version_id = (
                UUID(str(value)) for value in (project_id, chapter_id, document_id, version_id)
            )
        except (AttributeError, TypeError, ValueError):
            raise NotFoundError("Chapter document version not found.") from None
        if any(value.int == 0 for value in (project_id, chapter_id, document_id, version_id)):
            raise NotFoundError("Chapter document version not found.")
        document = await self.session.scalar(
            select(Document)
            .join(Chapter, Chapter.id == Document.chapter_id)
            .options(selectinload(Document.project))
            .where(
                Document.id == document_id,
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
                Document.type.in_(tuple(item.value for item in document_types)),
                Chapter.project_id == project_id,
            )
        )
        if document is None:
            raise NotFoundError("Chapter document version not found.")
        version = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
            )
        )
        if version is None:
            raise NotFoundError("Chapter document version not found.")
        if (
            type(version.byte_size) is not int
            or version.byte_size < 0
            or version.byte_size > MAX_CHAPTER_CONTENT_BYTES
        ):
            raise ChapterSegmentSnapshotMismatchError() from None
        snapshot_error = False
        try:
            snapshot_content = self._store_for(document).read_bounded(
                self._snapshot_path(version), max_bytes=MAX_CHAPTER_CONTENT_BYTES
            )
            normalized_content = normalize_chapter_content(snapshot_content)
        except Exception:
            snapshot_error = True
            normalized_content = ""
        if snapshot_error:
            raise ChapterSegmentSnapshotMismatchError() from None
        if (
            sha256_content(normalized_content) != version.content_hash
            or len(normalized_content.encode("utf-8")) != version.byte_size
        ):
            raise ChapterSegmentSnapshotMismatchError() from None
        return derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            content=normalized_content,
            segmenter_version=segmenter_version,
        )

    async def validate_chapter_evidence_segment_ids(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID,
        version_id: UUID,
        segmenter_version: str,
        segment_ids: Sequence[UUID],
    ) -> tuple[UUID, ...]:
        """Authoritatively validate evidence against a freshly derived exact snapshot."""

        segment_map = await self.derive_chapter_segment_map(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            segmenter_version=segmenter_version,
        )
        return validate_segment_map_evidence_integrity(
            segment_map,
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            version_id=version_id,
            segmenter_version=segmenter_version,
            segment_ids=segment_ids,
        )

    async def _append_version(
        self,
        *,
        document: Document,
        content: str,
        source: DocumentSource,
        actor_user_id: UUID | None,
        agent_role: str | None,
        workflow_run_id: UUID | None,
        change_summary: str | None,
        version_metadata: dict[str, object] | None = None,
    ) -> DocumentVersion:
        version = self._new_version(
            document=document,
            version_number=await self._next_version_number(document.id),
            parent_version_id=document.current_version_id,
            content=content,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary,
            version_metadata=version_metadata,
        )
        document.current_version = version
        self.session.add(version)
        await self._commit_with_file_writes(
            self._store_for(document), ((document.path, content), (version.snapshot_path, content))
        )
        return version

    async def _document(self, document_id: UUID) -> Document:
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(Document.id == document_id)
        )
        if document is None:
            raise NotFoundError("Document not found.")
        return document

    async def _locked_document(self, document_id: UUID) -> Document:
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.project), selectinload(Document.current_version))
            .where(Document.id == document_id)
            .with_for_update()
        )
        if document is None:
            raise NotFoundError("Document not found.")
        return document

    async def _next_version_number(self, document_id: UUID) -> int:
        latest = await self.session.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document_id
            )
        )
        return (latest or 0) + 1

    async def _lock_create_path(self, project_id: UUID, path: str) -> None:
        """Serialize creates for a project/path pair until this transaction ends."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"{project_id}:{path}"},
        )

    @staticmethod
    def _ensure_expected_current_version(
        document: Document, expected_current_version_id: UUID | None
    ) -> None:
        if document.current_version_id != expected_current_version_id:
            raise DocumentVersionConflictError("The document has a newer version.")

    @staticmethod
    def _ensure_document_path_is_not_reserved(path: str) -> None:
        if workspace_path_parts(path)[0] == ".versions":
            raise ReservedDocumentPathError()

    @staticmethod
    def _new_version(
        *,
        document: Document,
        version_number: int,
        parent_version_id: UUID | None,
        content: str,
        source: DocumentSource,
        actor_user_id: UUID | None,
        agent_role: str | None,
        workflow_run_id: UUID | None,
        change_summary: str | None,
        version_metadata: dict[str, object] | None = None,
    ) -> DocumentVersion:
        normalized_content = _normalize_content(content)
        snapshot = version_snapshot_path(str(document.id), version_number).as_posix()
        return DocumentVersion(
            document_id=document.id,
            version_number=version_number,
            parent_version_id=parent_version_id,
            source=source.value,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            content_hash=sha256_content(normalized_content),
            byte_size=len(normalized_content.encode("utf-8")),
            word_count=count_words(normalized_content),
            file_path=document.path,
            snapshot_path=snapshot,
            change_summary=change_summary,
            metadata_=dict(version_metadata or {}),
        )

    @staticmethod
    def _validated_version_metadata(
        metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        if metadata is None:
            return {}
        if (
            type(metadata) is not dict
            or set(metadata)
            not in (
                {"contract_version", "operation_key"},
                {"contract_version", "operation_key", "attempt_id"},
            )
            or type(metadata.get("contract_version")) is not str
            or _CONTRACT_VERSION_RE.fullmatch(metadata["contract_version"]) is None
            or type(metadata.get("operation_key")) is not str
            or _OPERATION_KEY_RE.fullmatch(metadata["operation_key"]) is None
            or (
                "attempt_id" in metadata
                and (
                    type(metadata["attempt_id"]) is not str
                    or not _is_canonical_nonzero_uuid(metadata["attempt_id"])
                )
            )
        ):
            raise DocumentVersionMetadataError() from None
        return dict(metadata)

    @staticmethod
    def _store_for(document: Document) -> MarkdownStore:
        assert document.project is not None
        return MarkdownStore(Path(document.project.workspace_root))

    @staticmethod
    def _snapshot_path(version: DocumentVersion) -> str:
        assert version.snapshot_path is not None
        return version.snapshot_path

    async def _commit_with_file_writes(
        self, store: MarkdownStore, writes: Sequence[tuple[str, str]]
    ) -> None:
        backups: list[_FileBackup] = []
        try:
            for path, content in writes:
                backups.append(_FileBackup(path, store.read(path) if store.exists(path) else None))
                store.write(path, content)
            await self.session.flush()
        except BaseException:
            await self.session.rollback()
            for backup in reversed(backups):
                try:
                    if backup.content is None:
                        store.delete(backup.path)
                    else:
                        store.write(backup.path, backup.content)
                except FileNotFoundError:
                    pass
            raise
        try:
            await self.session.commit()
        except BaseException as error:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            # A commit error has an unknown outcome.  Do not undo workspace files:
            # an outbox/reconciler is required for cross-resource reconciliation later.
            raise DocumentCommitIndeterminateError() from error


def _normalize_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")
