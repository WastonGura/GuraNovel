"""Unit tests for SettingContextResolver, SettingDocumentSnapshot, and SettingContextBundle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.agents.chapter_review_contracts import ReviewContextKind, ReviewerRole
from app.agents.chapter_writer_contracts import WriterContextKind
from app.core.errors import NotFoundError, WorkflowStateError
from app.models.core import Document, DocumentVersion, Project, SettingCollection
from app.models.enums import DocumentType
from app.services.document_service import DocumentService
from app.services.setting_context_resolver import (
    SettingContextBundle,
    SettingContextResolver,
    SettingDocumentSnapshot,
)
from app.workspace.hashing import sha256_content


def test_setting_document_snapshot_creation_and_hash() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "The empire was founded on ancient crystal magic."
    expected_hash = sha256_content(content)

    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World Overview",
        path="/settings/world.md",
        content=content,
        content_hash=expected_hash,
    )

    assert snapshot.content_hash == expected_hash
    assert snapshot.setting_collection_id == col_id
    assert snapshot.collection_revision == 1
    assert snapshot.document_id == doc_id
    assert snapshot.document_type == DocumentType.WORLD_OVERVIEW.value


def test_setting_document_snapshot_hash_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="content_hash does not match"):
        SettingDocumentSnapshot(
            setting_collection_id=uuid4(),
            collection_revision=1,
            document_id=uuid4(),
            version_id=uuid4(),
            document_type=DocumentType.WORLD_OVERVIEW.value,
            title="World Overview",
            path="/settings/world.md",
            content="Valid content",
            content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        )


def test_setting_document_snapshot_serialization_roundtrip() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Seven ranks of cultivation."
    c_hash = sha256_content(content)
    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=2,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power System",
        path="/settings/powers.md",
        content=content,
        content_hash=c_hash,
    )

    data = snapshot.to_dict()
    restored = SettingDocumentSnapshot.from_dict(data)

    assert restored == snapshot
    assert restored.setting_collection_id == col_id
    assert restored.collection_revision == 2
    assert restored.document_type == DocumentType.POWER_SYSTEM.value
    assert restored.content == "Seven ranks of cultivation."


def test_setting_context_bundle_creation_and_deterministic_hash() -> None:
    col_id = uuid4()
    content1 = "World lore content"
    content2 = "Power system content"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=3,
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World Lore",
        path="/settings/world.md",
        content=content1,
        content_hash=sha256_content(content1),
    )
    doc2 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=3,
        document_id=UUID("00000000-0000-0000-0000-000000000002"),
        version_id=uuid4(),
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power System",
        path="/settings/power.md",
        content=content2,
        content_hash=sha256_content(content2),
    )

    # Order in constructor does not affect bundle_hash
    bundle1 = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=3,
        documents=(doc1, doc2),
    )
    bundle2 = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=3,
        documents=(doc2, doc1),
    )

    assert bundle1.bundle_hash == bundle2.bundle_hash
    assert len(bundle1.bundle_hash) == 64


def test_setting_context_bundle_rejects_mixed_revisions() -> None:
    col_id = uuid4()
    c1 = "World lore"
    c2 = "Power system"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=c1,
        content_hash=sha256_content(c1),
    )
    doc2 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=2,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power",
        path="/settings/power.md",
        content=c2,
        content_hash=sha256_content(c2),
    )

    with pytest.raises(ValueError, match="Mixed collection revision in bundle"):
        SettingContextBundle(
            setting_collection_id=col_id,
            collection_revision=1,
            documents=(doc1, doc2),
        )


def test_setting_context_bundle_rejects_cross_collection_documents() -> None:
    col_id1 = uuid4()
    col_id2 = uuid4()
    c1 = "World lore"
    c2 = "Power system"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id1,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=c1,
        content_hash=sha256_content(c1),
    )
    doc2 = SettingDocumentSnapshot(
        setting_collection_id=col_id2,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power",
        path="/settings/power.md",
        content=c2,
        content_hash=sha256_content(c2),
    )

    with pytest.raises(ValueError, match="Cross-collection document in bundle"):
        SettingContextBundle(
            setting_collection_id=col_id1,
            collection_revision=1,
            documents=(doc1, doc2),
        )


def test_setting_context_bundle_rejects_duplicate_documents() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    c1 = "World lore"
    c2 = "Power system"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=c1,
        content_hash=sha256_content(c1),
    )
    doc2 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=uuid4(),
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power",
        path="/settings/power.md",
        content=c2,
        content_hash=sha256_content(c2),
    )

    with pytest.raises(ValueError, match="Duplicate document"):
        SettingContextBundle(
            setting_collection_id=col_id,
            collection_revision=1,
            documents=(doc1, doc2),
        )


def test_setting_context_bundle_serialization_roundtrip() -> None:
    col_id = uuid4()
    c = "World lore"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=2,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=c,
        content_hash=sha256_content(c),
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=2,
        documents=(doc1,),
    )

    data = bundle.to_dict()
    restored = SettingContextBundle.from_dict(data)

    assert restored == bundle
    assert restored.setting_collection_id == col_id
    assert restored.bundle_hash == bundle.bundle_hash


def test_setting_context_bundle_tampered_bundle_hash_rejected() -> None:
    col_id = uuid4()
    c = "World lore"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=2,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=c,
        content_hash=sha256_content(c),
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=2,
        documents=(doc1,),
    )
    data = bundle.to_dict()
    data["bundle_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"

    with pytest.raises(ValueError, match="Bundle hash mismatch"):
        SettingContextBundle.from_dict(data)


def test_setting_context_bundle_to_evidence() -> None:
    col_id = uuid4()
    c = "Imperial Guard vs Rebels"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.FACTIONS.value,
        title="Factions",
        path="/settings/factions.md",
        content=c,
        content_hash=sha256_content(c),
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(doc1,),
    )
    evidence = bundle.to_evidence()

    assert isinstance(evidence, dict)
    assert evidence["setting_collection_id"] == str(col_id)
    assert evidence["collection_revision"] == 1
    assert evidence["document_count"] == 1
    assert len(evidence["documents"]) == 1
    assert evidence["documents"][0]["document_type"] == "factions"


def test_setting_context_bundle_as_writer_and_review_contexts() -> None:
    project_id = uuid4()
    col_id = uuid4()
    c1 = "Ancient magic history"
    c2 = "Seven ranks of cultivation"
    doc1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.HISTORY.value,
        title="History",
        path="/settings/history.md",
        content=c1,
        content_hash=sha256_content(c1),
    )
    doc2 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=uuid4(),
        version_id=uuid4(),
        document_type=DocumentType.POWER_SYSTEM.value,
        title="Power System",
        path="/settings/power.md",
        content=c2,
        content_hash=sha256_content(c2),
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(doc1, doc2),
    )

    writer_contexts = bundle.as_writer_contexts(project_id)
    assert len(writer_contexts) == 2
    assert any(w.kind == WriterContextKind.TIMELINE for w in writer_contexts)
    assert any(w.kind == WriterContextKind.LORE_BOUNDARY for w in writer_contexts)

    review_contexts = bundle.as_review_contexts(project_id, ReviewerRole.LORE)
    assert len(review_contexts) == 2
    assert any(r.kind == ReviewContextKind.TIMELINE for r in review_contexts)
    assert any(r.kind == ReviewContextKind.LORE_BOUNDARY for r in review_contexts)

    # Style/chief editors should get empty review contexts for setting bundle
    editor_contexts = bundle.as_review_contexts(project_id, ReviewerRole.CHIEF_EDITOR)
    assert editor_contexts == ()


@pytest.mark.anyio
async def test_resolver_project_not_found() -> None:
    session = AsyncMock()
    session.get.return_value = None
    resolver = SettingContextResolver(session)

    with pytest.raises(NotFoundError, match="Project not found"):
        await resolver.resolve_for_project(uuid4())


@pytest.mark.anyio
async def test_resolver_bound_collection_not_found() -> None:
    project_id = uuid4()
    col_id = uuid4()
    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = col_id

    session = AsyncMock()
    session.get.side_effect = [project, None]
    resolver = SettingContextResolver(session)

    with pytest.raises(NotFoundError, match="Setting collection not found"):
        await resolver.resolve_for_project(project_id)


@pytest.mark.anyio
async def test_verify_snapshot_bundle_allows_older_revision() -> None:
    """Verify that verify_snapshot_bundle succeeds on an older snapshot even when

    the collection in DB has evolved to a newer revision.
    """
    project_id = uuid4()
    col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Old lore snapshot content"
    c_hash = sha256_content(content)

    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=content,
        content_hash=c_hash,
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(snapshot,),
    )

    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = col_id

    # In DB: SettingCollection is now at revision 5!
    collection = MagicMock(spec=SettingCollection)
    collection.id = col_id
    collection.revision = 5

    # In DB: Document exists and belongs to collection
    db_doc = MagicMock(spec=Document)
    db_doc.id = doc_id
    db_doc.setting_collection_id = col_id
    db_doc.type = DocumentType.WORLD_OVERVIEW.value
    db_doc.path = "/settings/world.md"

    # In DB: DocumentVersion exists with matching content_hash
    db_ver = MagicMock(spec=DocumentVersion)
    db_ver.id = ver_id
    db_ver.document_id = doc_id
    db_ver.content_hash = c_hash

    session = AsyncMock()
    session.get.side_effect = lambda model, ident: {
        (Project, project_id): project,
        (SettingCollection, col_id): collection,
        (Document, doc_id): db_doc,
    }.get((model, ident))
    session.scalar.return_value = db_ver

    resolver = SettingContextResolver(session)
    with patch.object(
        DocumentService, "read_version_content", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = content
        # Must succeed without throwing
        verified = await resolver.verify_snapshot_bundle(project_id, bundle)
        assert verified == bundle


@pytest.mark.anyio
async def test_verify_snapshot_bundle_rejects_hash_tampering() -> None:
    project_id = uuid4()
    col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Real content"
    c_hash = sha256_content(content)

    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=content,
        content_hash=c_hash,
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(snapshot,),
    )

    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = col_id

    collection = MagicMock(spec=SettingCollection)
    collection.id = col_id
    collection.revision = 1

    db_doc = MagicMock(spec=Document)
    db_doc.id = doc_id
    db_doc.setting_collection_id = col_id
    db_doc.type = DocumentType.WORLD_OVERVIEW.value
    db_doc.path = "/settings/world.md"

    db_ver = MagicMock(spec=DocumentVersion)
    db_ver.id = ver_id
    db_ver.document_id = doc_id
    db_ver.content_hash = "different_hash_on_disk"

    session = AsyncMock()
    session.get.side_effect = lambda model, ident: {
        (Project, project_id): project,
        (SettingCollection, col_id): collection,
        (Document, doc_id): db_doc,
    }.get((model, ident))
    session.scalar.return_value = db_ver

    resolver = SettingContextResolver(session)
    with pytest.raises(WorkflowStateError, match="content hash mismatch"):
        await resolver.verify_snapshot_bundle(project_id, bundle)


@pytest.mark.anyio
async def test_verify_snapshot_bundle_rejects_foreign_document() -> None:
    project_id = uuid4()
    col_id = uuid4()
    foreign_col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Lore content"
    c_hash = sha256_content(content)

    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="World",
        path="/settings/world.md",
        content=content,
        content_hash=c_hash,
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(snapshot,),
    )

    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = col_id

    collection = MagicMock(spec=SettingCollection)
    collection.id = col_id
    collection.revision = 1

    db_doc = MagicMock(spec=Document)
    db_doc.id = doc_id
    db_doc.setting_collection_id = foreign_col_id  # foreign!
    db_doc.type = DocumentType.WORLD_OVERVIEW.value
    db_doc.path = "/settings/world.md"

    session = AsyncMock()
    session.get.side_effect = lambda model, ident: {
        (Project, project_id): project,
        (SettingCollection, col_id): collection,
        (Document, doc_id): db_doc,
    }.get((model, ident))

    resolver = SettingContextResolver(session)
    with pytest.raises(WorkflowStateError, match="belongs to collection"):
        await resolver.verify_snapshot_bundle(project_id, bundle)


@pytest.mark.anyio
async def test_load_bundle_from_evidence_roundtrip() -> None:
    project_id = uuid4()
    col_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Factions lore content"
    c_hash = sha256_content(content)

    snapshot = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=1,
        document_id=doc_id,
        version_id=ver_id,
        document_type=DocumentType.FACTIONS.value,
        title="Factions",
        path="/settings/factions.md",
        content=content,
        content_hash=c_hash,
    )
    bundle = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=1,
        documents=(snapshot,),
    )
    evidence = bundle.to_evidence()

    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = col_id

    collection = MagicMock(spec=SettingCollection)
    collection.id = col_id
    collection.revision = 1

    db_doc = MagicMock(spec=Document)
    db_doc.id = doc_id
    db_doc.setting_collection_id = col_id
    db_doc.type = DocumentType.FACTIONS.value
    db_doc.path = "/settings/factions.md"

    db_ver = MagicMock(spec=DocumentVersion)
    db_ver.id = ver_id
    db_ver.document_id = doc_id
    db_ver.content_hash = c_hash

    session = AsyncMock()
    session.get.side_effect = lambda model, ident: {
        (Project, project_id): project,
        (SettingCollection, col_id): collection,
        (Document, doc_id): db_doc,
    }.get((model, ident))
    session.scalar.return_value = db_ver

    resolver = SettingContextResolver(session)
    with patch.object(
        DocumentService, "read_version_content", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = content
        loaded = await resolver.load_bundle_from_evidence(project_id, evidence)
        assert loaded.setting_collection_id == col_id
        assert loaded.collection_revision == 1
        assert len(loaded.documents) == 1
        assert loaded.documents[0].content == content


@pytest.mark.anyio
async def test_load_bundle_from_evidence_corrupt_payload() -> None:
    session = AsyncMock()
    resolver = SettingContextResolver(session)

    with pytest.raises(WorkflowStateError, match="Invalid evidence"):
        await resolver.load_bundle_from_evidence(uuid4(), "not-a-dict")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_resolve_for_project_legacy_fallback() -> None:
    project_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    content = "Legacy world content"
    c_hash = sha256_content(content)

    project = MagicMock(spec=Project)
    project.id = project_id
    project.setting_collection_id = None  # Legacy project without bound collection

    db_doc = MagicMock(spec=Document)
    db_doc.id = doc_id
    db_doc.project_id = project_id
    db_doc.type = DocumentType.WORLD_OVERVIEW.value
    db_doc.path = "/lore/world.md"
    db_doc.title = "World Overview"
    db_doc.current_version_id = ver_id

    db_ver = MagicMock(spec=DocumentVersion)
    db_ver.id = ver_id
    db_ver.document_id = doc_id
    db_ver.content_hash = c_hash

    session = AsyncMock()
    session.get.side_effect = lambda model, ident: {
        (Project, project_id): project,
        (Document, doc_id): db_doc,
        (DocumentVersion, ver_id): db_ver,
    }.get((model, ident))
    session.scalars.return_value = [db_doc]
    session.scalar.return_value = db_ver

    resolver = SettingContextResolver(session)
    with patch.object(
        DocumentService, "read_version_content", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = content
        bundle = await resolver.resolve_for_project(project_id)
        assert bundle.setting_collection_id == project_id
        assert bundle.collection_revision == 1
        assert len(bundle.documents) == 1
        assert bundle.documents[0].content == content

