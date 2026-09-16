"""Unified setting context resolution and task snapshot management.

Resolves the latest setting collection documents for novel projects and verifies
that task replays, retries, and restarts use strictly consistent, unforgeable snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chapter_review_contracts import (
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewerRole,
)
from app.agents.chapter_writer_contracts import (
    WriterContextKind,
    WriterContextSnapshot,
)
from app.core.errors import NotFoundError, WorkflowStateError
from app.models import Document, DocumentVersion, Project, SettingCollection
from app.models.enums import DocumentType
from app.services.document_service import DocumentService
from app.workspace.hashing import sha256_content


SETTING_DOCUMENT_TYPES: frozenset[DocumentType] = frozenset(
    {
        DocumentType.WORLD_OVERVIEW,
        DocumentType.POWER_SYSTEM,
        DocumentType.FACTIONS,
        DocumentType.GEOGRAPHY,
        DocumentType.HISTORY,
        DocumentType.CHARACTER_PROFILE,
        DocumentType.GLOSSARY,
    }
)

_SETTING_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in SETTING_DOCUMENT_TYPES)


def _valid_uuid(value: object) -> bool:
    return isinstance(value, UUID) and value.int != 0


def _canonical_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        if value.int == 0:
            raise ValueError("UUID must be non-zero")
        return UUID(int=value.int)
    if isinstance(value, str):
        parsed = UUID(value.strip())
        if parsed.int == 0:
            raise ValueError("UUID must be non-zero")
        return parsed
    raise ValueError(f"Invalid UUID value: {value!r}")


@dataclass(frozen=True, slots=True)
class SettingDocumentSnapshot:
    """Immutable snapshot of one setting collection document version."""

    setting_collection_id: UUID
    collection_revision: int
    document_id: UUID
    version_id: UUID
    document_type: str
    title: str
    path: str
    content: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "setting_collection_id", _canonical_uuid(self.setting_collection_id))
        object.__setattr__(self, "document_id", _canonical_uuid(self.document_id))
        object.__setattr__(self, "version_id", _canonical_uuid(self.version_id))
        if not isinstance(self.collection_revision, int) or self.collection_revision < 0:
            raise ValueError("collection_revision must be a non-negative integer")
        if not isinstance(self.document_type, str) or not self.document_type.strip():
            raise ValueError("document_type must be a non-empty string")
        if not isinstance(self.title, str):
            raise ValueError("title must be a string")
        if not isinstance(self.path, str):
            raise ValueError("path must be a string")
        if not isinstance(self.content, str):
            raise ValueError("content must be a string")
        if not isinstance(self.content_hash, str) or len(self.content_hash) != 64:
            raise ValueError("content_hash must be a 64-char hex string")
        expected_hash = sha256_content(self.content)
        if self.content_hash.lower() != expected_hash.lower():
            raise ValueError("content_hash does not match hash of content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "setting_collection_id": str(self.setting_collection_id),
            "collection_revision": self.collection_revision,
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "document_type": self.document_type,
            "title": self.title,
            "path": self.path,
            "content": self.content,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SettingDocumentSnapshot:
        return cls(
            setting_collection_id=_canonical_uuid(data["setting_collection_id"]),
            collection_revision=int(data["collection_revision"]),
            document_id=_canonical_uuid(data["document_id"]),
            version_id=_canonical_uuid(data["version_id"]),
            document_type=str(data["document_type"]),
            title=str(data["title"]),
            path=str(data["path"]),
            content=str(data["content"]),
            content_hash=str(data["content_hash"]),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class SettingContextBundle:
    """Bounded, multi-document setting context bundle for a specific collection revision."""

    setting_collection_id: UUID
    collection_revision: int
    documents: tuple[SettingDocumentSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "setting_collection_id", _canonical_uuid(self.setting_collection_id))
        if not isinstance(self.collection_revision, int) or self.collection_revision < 0:
            raise ValueError("collection_revision must be a non-negative integer")
        seen_doc_ids: set[UUID] = set()
        for doc in self.documents:
            if not isinstance(doc, SettingDocumentSnapshot):
                raise ValueError(f"Expected SettingDocumentSnapshot, got {type(doc)}")
            if doc.setting_collection_id != self.setting_collection_id:
                raise ValueError(
                    f"Cross-collection document in bundle: doc {doc.document_id} belongs to "
                    f"{doc.setting_collection_id}, expected {self.setting_collection_id}"
                )
            if doc.collection_revision != self.collection_revision:
                raise ValueError(
                    f"Mixed collection revision in bundle: doc {doc.document_id} has revision "
                    f"{doc.collection_revision}, expected {self.collection_revision}"
                )
            if doc.document_id in seen_doc_ids:
                raise ValueError(f"Duplicate document {doc.document_id} in setting context bundle")
            seen_doc_ids.add(doc.document_id)

    @property
    def bundle_hash(self) -> str:
        sorted_docs = sorted(
            self.documents,
            key=lambda d: (d.document_type, d.path, str(d.document_id)),
        )
        digest_input = f"{self.setting_collection_id}:{self.collection_revision}:" + ";".join(
            f"{d.document_id}:{d.version_id}:{d.content_hash}" for d in sorted_docs
        )
        return sha256_content(digest_input)

    def get_by_type(self, doc_type: DocumentType | str) -> tuple[SettingDocumentSnapshot, ...]:
        type_str = doc_type.value if isinstance(doc_type, DocumentType) else str(doc_type)
        return tuple(d for d in self.documents if d.document_type == type_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setting_collection_id": str(self.setting_collection_id),
            "collection_revision": self.collection_revision,
            "bundle_hash": self.bundle_hash,
            "documents": [d.to_dict() for d in self.documents],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SettingContextBundle:
        docs = tuple(
            SettingDocumentSnapshot.from_dict(item)
            for item in data.get("documents", ())
        )
        bundle = cls(
            setting_collection_id=_canonical_uuid(data["setting_collection_id"]),
            collection_revision=int(data["collection_revision"]),
            documents=docs,
        )
        expected_hash = data.get("bundle_hash")
        if expected_hash and bundle.bundle_hash != expected_hash:
            raise ValueError(
                f"Bundle hash mismatch: expected {expected_hash}, computed {bundle.bundle_hash}"
            )
        return bundle

    def to_evidence(self) -> dict[str, Any]:
        return {
            "setting_collection_id": str(self.setting_collection_id),
            "collection_revision": self.collection_revision,
            "bundle_hash": self.bundle_hash,
            "document_count": len(self.documents),
            "documents": [
                {
                    "document_id": str(d.document_id),
                    "version_id": str(d.version_id),
                    "content_hash": d.content_hash,
                    "document_type": d.document_type,
                    "path": d.path,
                    "title": d.title,
                }
                for d in self.documents
            ],
        }

    def as_writer_contexts(self, project_id: UUID) -> tuple[WriterContextSnapshot, ...]:
        snapshots: list[WriterContextSnapshot] = []
        canonical_project_id = _canonical_uuid(project_id)
        for doc in self.documents:
            stripped = doc.content.strip()
            if not stripped:
                continue
            if doc.document_type == DocumentType.CHARACTER_PROFILE.value:
                kind = WriterContextKind.CHARACTER_STATE
            elif doc.document_type == DocumentType.HISTORY.value:
                kind = WriterContextKind.TIMELINE
            else:
                kind = WriterContextKind.LORE_BOUNDARY
            content = stripped if len(stripped) <= 32_768 else stripped[:32_768].rsplit(" ", 1)[0]
            snapshots.append(
                WriterContextSnapshot(
                    project_id=canonical_project_id,
                    document_id=doc.document_id,
                    version_id=doc.version_id,
                    kind=kind,
                    content=content,
                )
            )
        return tuple(snapshots)

    def as_review_contexts(
        self, project_id: UUID, role: ReviewerRole | None = None
    ) -> tuple[ReviewContextSnapshot, ...]:
        if role in {ReviewerRole.EDITOR, ReviewerRole.CHIEF_EDITOR}:
            return ()
        snapshots: list[ReviewContextSnapshot] = []
        canonical_project_id = _canonical_uuid(project_id)
        for doc in self.documents:
            stripped = doc.content.strip()
            if not stripped:
                continue
            if doc.document_type == DocumentType.CHARACTER_PROFILE.value:
                kind = ReviewContextKind.CHARACTER_STATE
            elif doc.document_type == DocumentType.HISTORY.value:
                kind = ReviewContextKind.TIMELINE
            else:
                kind = ReviewContextKind.LORE_BOUNDARY
            content = stripped if len(stripped) <= 32_768 else stripped[:32_768].rsplit(" ", 1)[0]
            snapshots.append(
                ReviewContextSnapshot(
                    project_id=canonical_project_id,
                    document_id=doc.document_id,
                    version_id=doc.version_id,
                    kind=kind,
                    content=content,
                )
            )
        return tuple(snapshots)


class SettingContextResolver:
    """Service to resolve and verify evolving setting collection snapshots for agent runs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_for_project(
        self,
        project_id: UUID,
        allowed_types: Sequence[DocumentType | str] | None = None,
    ) -> SettingContextBundle:
        """Resolve current setting documents for a project's bound setting collection."""
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")

        if project.setting_collection_id is not None:
            collection_bundle = await self.resolve_for_collection(
                project.setting_collection_id, allowed_types=allowed_types
            )
            if collection_bundle.documents:
                return collection_bundle

        # Legacy fallback when project has no setting_collection_id bound or collection has no documents
        target_collection_id = project.setting_collection_id or project_id
        types_to_query = (
            [t.value if isinstance(t, DocumentType) else str(t) for t in allowed_types]
            if allowed_types
            else [t.value for t in SETTING_DOCUMENT_TYPES]
        )
        documents = list(
            await self.session.scalars(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.type.in_(types_to_query),
                    Document.current_version_id.is_not(None),
                )
                .order_by(Document.type, Document.path, Document.id)
                .limit(32)
            )
        )
        doc_service = DocumentService(self.session)
        snapshots: list[SettingDocumentSnapshot] = []
        for doc in documents:
            version_id = doc.current_version_id
            if version_id is None:
                continue
            version = await self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == doc.id,
                )
            )
            if version is None:
                raise WorkflowStateError(f"Current version for document {doc.id} not found.")
            content = await doc_service.read_version_content(doc.id, version.id)
            if content is None:
                content = ""
            actual_hash = sha256_content(content)
            if actual_hash != version.content_hash:
                raise WorkflowStateError(f"Content hash mismatch for legacy document {doc.id}.")
            snapshots.append(
                SettingDocumentSnapshot(
                    setting_collection_id=target_collection_id,
                    collection_revision=1,
                    document_id=doc.id,
                    version_id=version.id,
                    document_type=doc.type,
                    title=doc.title or "",
                    path=doc.path,
                    content=content,
                    content_hash=version.content_hash,
                    metadata=doc.metadata_ if isinstance(doc.metadata_, dict) else {},
                )
            )
        return SettingContextBundle(
            setting_collection_id=target_collection_id,
            collection_revision=1,
            documents=tuple(snapshots),
        )

    async def resolve_for_collection(
        self,
        setting_collection_id: UUID,
        allowed_types: Sequence[DocumentType | str] | None = None,
    ) -> SettingContextBundle:
        """Resolve current setting documents directly for a setting collection."""
        collection = await self.session.get(SettingCollection, setting_collection_id)
        if collection is None:
            raise NotFoundError("Setting collection not found.")

        types_to_query = (
            [t.value if isinstance(t, DocumentType) else str(t) for t in allowed_types]
            if allowed_types
            else [t.value for t in SETTING_DOCUMENT_TYPES]
        )
        documents = list(
            await self.session.scalars(
                select(Document)
                .where(
                    Document.setting_collection_id == setting_collection_id,
                    Document.type.in_(types_to_query),
                    Document.current_version_id.is_not(None),
                )
                .order_by(Document.type, Document.path, Document.id)
                .limit(64)
            )
        )
        doc_service = DocumentService(self.session)
        snapshots: list[SettingDocumentSnapshot] = []
        for doc in documents:
            version_id = doc.current_version_id
            if version_id is None:
                continue
            version = await self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == doc.id,
                )
            )
            if version is None:
                raise WorkflowStateError(f"Current version for document {doc.id} not found.")
            content = await doc_service.read_version_content(doc.id, version.id)
            if content is None:
                content = ""
            actual_hash = sha256_content(content)
            if actual_hash != version.content_hash:
                raise WorkflowStateError(f"Content hash mismatch for document {doc.id}.")
            snapshots.append(
                SettingDocumentSnapshot(
                    setting_collection_id=setting_collection_id,
                    collection_revision=collection.revision,
                    document_id=doc.id,
                    version_id=version.id,
                    document_type=doc.type,
                    title=doc.title,
                    path=doc.path,
                    content=content,
                    content_hash=version.content_hash,
                    metadata=doc.metadata_ if isinstance(doc.metadata_, dict) else {},
                )
            )
        return SettingContextBundle(
            setting_collection_id=setting_collection_id,
            collection_revision=collection.revision,
            documents=tuple(snapshots),
        )

    async def verify_snapshot_bundle(
        self,
        project_id: UUID,
        bundle_or_data: SettingContextBundle | dict[str, Any],
    ) -> SettingContextBundle:
        """Verify an existing setting context snapshot for retries, recovery, or restart.

        Guarantees:
        1. All documents belong to the setting collection referenced by the bundle.
        2. All documents share the same collection revision (no mixed revisions).
        3. The setting collection matches the project's bound setting collection.
        4. All referenced document versions exist, have matching content hashes, and match actual content.
        5. Does NOT reject if the setting collection evolved to a higher revision later.
        """
        if isinstance(bundle_or_data, dict):
            try:
                bundle = SettingContextBundle.from_dict(bundle_or_data)
            except Exception as e:
                raise WorkflowStateError(f"Invalid setting context snapshot format: {e}") from None
        elif isinstance(bundle_or_data, SettingContextBundle):
            bundle = bundle_or_data
        else:
            raise WorkflowStateError("Expected SettingContextBundle or dictionary snapshot.")

        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")

        # If project has bound setting collection, verify collection ID matches
        if project.setting_collection_id is not None:
            if bundle.setting_collection_id != project.setting_collection_id:
                raise WorkflowStateError(
                    f"Snapshot setting_collection_id {bundle.setting_collection_id} "
                    f"does not match project setting_collection_id {project.setting_collection_id}"
                )
        else:
            if bundle.setting_collection_id != project_id:
                raise WorkflowStateError("Legacy snapshot belongs to a different project.")

        doc_service = DocumentService(self.session)
        for doc_snap in bundle.documents:
            # Check revision uniformity
            if doc_snap.collection_revision != bundle.collection_revision:
                raise WorkflowStateError("Mixed collection revisions detected in snapshot.")
            if doc_snap.setting_collection_id != bundle.setting_collection_id:
                raise WorkflowStateError("Cross-collection document detected in snapshot.")

            # Check document existence and ownership in DB
            doc = await self.session.get(Document, doc_snap.document_id)
            if doc is None:
                raise WorkflowStateError(f"Document {doc_snap.document_id} not found in database.")
            if project.setting_collection_id is not None:
                if (
                    doc.setting_collection_id != bundle.setting_collection_id
                    and doc.project_id != project_id
                ):
                    raise WorkflowStateError(
                        f"Document {doc.id} belongs to collection {doc.setting_collection_id}, "
                        f"not {bundle.setting_collection_id}"
                    )
            else:
                if doc.project_id != project_id:
                    raise WorkflowStateError(
                        f"Document {doc.id} belongs to project {doc.project_id}, not {project_id}"
                    )

            # Check version existence and content hash in DB
            version = await self.session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == doc_snap.version_id,
                    DocumentVersion.document_id == doc.id,
                )
            )
            if version is None:
                raise WorkflowStateError(
                    f"DocumentVersion {doc_snap.version_id} not found for document {doc.id}."
                )
            if version.content_hash.lower() != doc_snap.content_hash.lower():
                raise WorkflowStateError(
                    f"DocumentVersion content hash mismatch for document {doc.id}."
                )

            # Read actual persisted content and verify it matches the snapshot
            actual_content = await doc_service.read_version_content(doc.id, version.id)
            if actual_content is None:
                actual_content = ""
            if sha256_content(actual_content).lower() != doc_snap.content_hash.lower():
                raise WorkflowStateError(
                    f"Persisted content on disk/storage does not match hash for document {doc.id}."
                )
            if actual_content != doc_snap.content:
                raise WorkflowStateError(
                    f"Persisted content differs from snapshot content for document {doc.id}."
                )

        return bundle

    async def load_bundle_from_evidence(
        self,
        project_id: UUID,
        evidence: dict[str, Any],
    ) -> SettingContextBundle:
        """Reconstruct and strictly verify a SettingContextBundle from stored evidence."""
        if not isinstance(evidence, dict):
            raise WorkflowStateError("Invalid evidence payload: expected dictionary.")
        try:
            setting_collection_id = _canonical_uuid(evidence["setting_collection_id"])
            collection_revision = int(evidence["collection_revision"])
        except (KeyError, TypeError, ValueError) as e:
            raise WorkflowStateError(f"Invalid setting evidence metadata: {e}") from None

        doc_items = evidence.get("documents", [])
        if not isinstance(doc_items, (list, tuple)):
            raise WorkflowStateError("Invalid setting evidence documents list.")

        doc_service = DocumentService(self.session)
        snapshots: list[SettingDocumentSnapshot] = []
        for item in doc_items:
            try:
                doc_id = _canonical_uuid(item["document_id"])
                ver_id = _canonical_uuid(item["version_id"])
                expected_hash = str(item["content_hash"])
                doc_type = str(item.get("document_type") or item.get("type", ""))
                path = str(item.get("path", ""))
                title = str(item.get("title", ""))
            except (KeyError, TypeError, ValueError) as e:
                raise WorkflowStateError(f"Invalid setting document snapshot item: {e}") from None

            content = item.get("content")
            if content is None:
                try:
                    content = await doc_service.read_version_content(doc_id, ver_id)
                except Exception as e:
                    raise WorkflowStateError(f"Version content for document {doc_id} not found: {e}") from None
                if content is None:
                    raise WorkflowStateError(f"Version content for document {doc_id} not found.")

            try:
                snapshots.append(
                    SettingDocumentSnapshot(
                        setting_collection_id=setting_collection_id,
                        collection_revision=collection_revision,
                        document_id=doc_id,
                        version_id=ver_id,
                        document_type=doc_type,
                        title=title,
                        path=path,
                        content=content,
                        content_hash=expected_hash,
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
            except ValueError as e:
                raise WorkflowStateError(f"Tampered setting document snapshot: {e}") from None

        try:
            bundle = SettingContextBundle(
                setting_collection_id=setting_collection_id,
                collection_revision=collection_revision,
                documents=tuple(snapshots),
            )
        except ValueError as e:
            raise WorkflowStateError(f"Tampered setting bundle: {e}") from None

        return await self.verify_snapshot_bundle(project_id, bundle)



__all__ = [
    "SETTING_DOCUMENT_TYPES",
    "SettingDocumentSnapshot",
    "SettingContextBundle",
    "SettingContextResolver",
]
