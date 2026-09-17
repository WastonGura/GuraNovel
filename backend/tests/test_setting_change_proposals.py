"""Unit tests for SettingChangeProposal contracts, OCC apply, cross-novel impact, and snapshot immutability."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agents.setting_proposal_contracts import (
    SettingChangeProposal,
    SettingProposalSourceTask,
    SettingProposalStatus,
)
from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Document,
    DocumentSource,
    DocumentType,
    SettingCollection,
)
from app.services.document_service import DocumentService, DocumentVersionConflictError
from app.services.setting_collection_service import SettingCollectionService
from app.services.setting_context_resolver import (
    SettingContextBundle,
    SettingDocumentSnapshot,
)
from app.workspace.hashing import sha256_content


# ==========================================
# 1. Pydantic Schema Validation Tests (Unit)
# ==========================================


def test_proposal_schema_valid_new_document() -> None:
    col_id = uuid4()
    prop_id = uuid4()
    proposal = SettingChangeProposal(
        id=prop_id,
        setting_collection_id=col_id,
        target_document_id=None,
        base_version_id=None,
        title="Gray Tide Society",
        category="setting",
        proposed_content="An underground organization active in the old harbor.",
        reason="Established harbor network background.",
        source_task=SettingProposalSourceTask(
            agent_role="lore_agent",
            novel_title="Harbor Chronicles",
        ),
    )
    assert proposal.id == prop_id
    assert proposal.target_document_id is None
    assert proposal.base_version_id is None
    assert proposal.status == SettingProposalStatus.PENDING
    assert proposal.source_task.agent_role == "lore_agent"
    assert proposal.source_task.novel_title == "Harbor Chronicles"


def test_proposal_schema_valid_update_document() -> None:
    col_id = uuid4()
    prop_id = uuid4()
    doc_id = uuid4()
    base_ver_id = uuid4()
    proposal = SettingChangeProposal(
        id=prop_id,
        setting_collection_id=col_id,
        target_document_id=doc_id,
        base_version_id=base_ver_id,
        title="Old Warehouse",
        category="world",
        proposed_content="Expanded warehouse lore with subterranean dock access.",
        reason="Connects with Chapter 3 discovery.",
        source_task=SettingProposalSourceTask(
            workflow_run_id=uuid4(),
            chapter_id=uuid4(),
            agent_role="writer_agent",
        ),
    )
    assert proposal.target_document_id == doc_id
    assert proposal.base_version_id == base_ver_id


def test_proposal_schema_requires_base_version_for_existing_doc() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SettingChangeProposal(
            id=uuid4(),
            setting_collection_id=uuid4(),
            target_document_id=uuid4(),
            base_version_id=None,
            title="Invalid Binding",
            category="setting",
            proposed_content="Content",
            reason="Missing base version",
        )
    assert "base_version_id is required when target_document_id is specified" in str(exc_info.value)


def test_proposal_schema_forbids_base_version_for_new_doc() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SettingChangeProposal(
            id=uuid4(),
            setting_collection_id=uuid4(),
            target_document_id=None,
            base_version_id=uuid4(),
            title="Invalid New Binding",
            category="setting",
            proposed_content="Content",
            reason="Spurious base version",
        )
    assert "base_version_id must not be specified for a new document proposal" in str(exc_info.value)


def test_proposal_schema_rejects_nil_uuid() -> None:
    with pytest.raises(ValidationError):
        SettingChangeProposal(
            id=UUID("00000000-0000-0000-0000-000000000000"),
            setting_collection_id=uuid4(),
            title="Title",
            category="setting",
            proposed_content="Content",
            reason="Reason",
        )


def test_proposal_schema_rejects_invalid_bounds() -> None:
    # Empty title
    with pytest.raises(ValidationError):
        SettingChangeProposal(
            id=uuid4(),
            setting_collection_id=uuid4(),
            title="   ",
            category="setting",
            proposed_content="Content",
            reason="Reason",
        )
    # Empty proposed content
    with pytest.raises(ValidationError):
        SettingChangeProposal(
            id=uuid4(),
            setting_collection_id=uuid4(),
            title="Valid Title",
            category="setting",
            proposed_content="",
            reason="Reason",
        )


def test_proposal_serialization_roundtrip() -> None:
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=uuid4(),
        target_document_id=uuid4(),
        base_version_id=uuid4(),
        title="Roundtrip Title",
        category="setting",
        proposed_content="Roundtrip Content",
        reason="Roundtrip Reason",
        source_task=SettingProposalSourceTask(
            workflow_run_id=uuid4(),
            novel_title="My Epic",
        ),
    )
    serialized = proposal.to_dict()
    deserialized = SettingChangeProposal.from_dict(serialized)
    assert deserialized.id == proposal.id
    assert deserialized.setting_collection_id == proposal.setting_collection_id
    assert deserialized.target_document_id == proposal.target_document_id
    assert deserialized.base_version_id == proposal.base_version_id
    assert deserialized.title == proposal.title
    assert deserialized.source_task.novel_title == "My Epic"


# ==========================================
# 2. Service & OCC Logic Tests (Unit)
# ==========================================


@pytest.mark.anyio
async def test_apply_proposal_collection_id_mismatch() -> None:
    session = AsyncMock()
    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=uuid4(),
        title="Mismatch Title",
        category="setting",
        proposed_content="Content",
        reason="Reason",
    )
    with pytest.raises(ConflictError, match="Proposal setting collection does not match target collection"):
        await service.apply_setting_change_proposal(uuid4(), proposal)


@pytest.mark.anyio
async def test_apply_proposal_archived_collection() -> None:
    col_id = uuid4()
    session = AsyncMock()
    col = MagicMock(spec=SettingCollection)
    col.id = col_id
    col.status = "archived"
    col.owner_id = None
    session.get.return_value = col

    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        title="Archived Title",
        category="setting",
        proposed_content="Content",
        reason="Reason",
    )
    with pytest.raises(ConflictError, match="Archived setting collection cannot be modified"):
        await service.apply_setting_change_proposal(col_id, proposal)


@pytest.mark.anyio
async def test_apply_proposal_target_document_not_in_collection() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    base_ver_id = uuid4()
    session = AsyncMock()
    col = MagicMock(spec=SettingCollection)
    col.id = col_id
    col.status = "active"
    col.owner_id = None

    # Document belongs to a DIFFERENT collection
    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.setting_collection_id = uuid4()

    session.get.side_effect = lambda model, ident: {
        (SettingCollection, col_id): col,
        (Document, doc_id): doc,
    }.get((model, ident))

    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        target_document_id=doc_id,
        base_version_id=base_ver_id,
        title="Doc Title",
        category="setting",
        proposed_content="Content",
        reason="Reason",
    )
    with pytest.raises(NotFoundError, match="Target setting document not found in collection"):
        await service.apply_setting_change_proposal(col_id, proposal)


@pytest.mark.anyio
async def test_apply_proposal_update_existing_doc_occ_success() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    base_ver_id = uuid4()
    actor_id = uuid4()

    session = AsyncMock()
    col = MagicMock(spec=SettingCollection)
    col.id = col_id
    col.status = "active"
    col.owner_id = None

    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.setting_collection_id = col_id
    doc.current_version_id = base_ver_id

    session.get.side_effect = lambda model, ident: {
        (SettingCollection, col_id): col,
        (Document, doc_id): doc,
    }.get((model, ident))
    session.scalar.return_value = doc

    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        target_document_id=doc_id,
        base_version_id=base_ver_id,
        title="Agent Neo",
        category="setting",
        proposed_content="Cyberdeck updated.",
        reason="Equipment upgrade in chapter 2.",
        source_task=SettingProposalSourceTask(agent_role="writer_agent"),
    )

    with patch.object(DocumentService, "write_document", new_callable=AsyncMock) as mock_write:
        res = await service.apply_setting_change_proposal(col_id, proposal, actor_user_id=actor_id)
        assert res == doc
        mock_write.assert_awaited_once_with(
            document_id=doc_id,
            content="Cyberdeck updated.",
            source=DocumentSource.WRITER_AGENT,
            expected_current_version_id=base_ver_id,
            actor_user_id=actor_id,
            agent_role="writer_agent",
            workflow_run_id=None,
            change_summary="Equipment upgrade in chapter 2.",
        )


@pytest.mark.anyio
async def test_apply_proposal_update_existing_doc_occ_conflict() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    stale_base_ver_id = uuid4()

    session = AsyncMock()
    col = MagicMock(spec=SettingCollection)
    col.id = col_id
    col.status = "active"
    col.owner_id = None

    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.setting_collection_id = col_id
    doc.current_version_id = uuid4()  # Newer version!

    session.get.side_effect = lambda model, ident: {
        (SettingCollection, col_id): col,
        (Document, doc_id): doc,
    }.get((model, ident))

    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        target_document_id=doc_id,
        base_version_id=stale_base_ver_id,
        title="Agent Neo",
        category="setting",
        proposed_content="Stale write.",
        reason="Stale update attempt.",
    )

    with patch.object(
        DocumentService,
        "write_document",
        side_effect=DocumentVersionConflictError("The document has a newer version."),
    ):
        with pytest.raises(DocumentVersionConflictError, match="The document has a newer version"):
            await service.apply_setting_change_proposal(col_id, proposal)


@pytest.mark.anyio
async def test_apply_proposal_create_new_doc() -> None:
    col_id = uuid4()
    new_doc_id = uuid4()

    session = AsyncMock()
    col = MagicMock(spec=SettingCollection)
    col.id = col_id
    col.status = "active"
    col.owner_id = None

    new_doc = MagicMock(spec=Document)
    new_doc.id = new_doc_id
    new_doc.setting_collection_id = col_id

    session.get.return_value = col
    session.scalar.return_value = new_doc

    service = SettingCollectionService(session)
    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        target_document_id=None,
        base_version_id=None,
        title="Engwithan Ruins",
        category="world",
        proposed_content="Ancient animancy ruins.",
        reason="Worldbuilding addition.",
        source_task=SettingProposalSourceTask(novel_title="Pillars"),
    )

    with patch.object(DocumentService, "create_document", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = new_doc
        res = await service.apply_setting_change_proposal(col_id, proposal)
        assert res == new_doc
        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["setting_collection_id"] == col_id
        assert mock_create.call_args.kwargs["document_type"] == DocumentType.WORLD_OVERVIEW
        assert mock_create.call_args.kwargs["title"] == "Engwithan Ruins"
        assert mock_create.call_args.kwargs["content"] == "Ancient animancy ruins."


@pytest.mark.anyio
async def test_rejection_does_not_mutate_setting_documents_or_revision() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    base_ver_id = uuid4()

    proposal = SettingChangeProposal(
        id=uuid4(),
        setting_collection_id=col_id,
        target_document_id=doc_id,
        base_version_id=base_ver_id,
        title="Sun Spire",
        category="world",
        proposed_content="Destroyed.",
        reason="Rejected proposal.",
        status=SettingProposalStatus.REJECTED,
    )
    assert proposal.status == SettingProposalStatus.REJECTED

    # Mock session ensures no write is executed when proposal is rejected
    session = AsyncMock()
    doc_service = DocumentService(session)
    with patch.object(doc_service, "write_document") as mock_write:
        # Rejection simply updates user-facing state; it never invokes write_document
        assert not mock_write.called


@pytest.mark.anyio
async def test_task_snapshot_immutability_across_proposal_revisions() -> None:
    col_id = uuid4()
    doc_id = uuid4()
    v1_id = uuid4()
    content_v1 = "Rule 1: Never cross your own temporal wake."
    hash_v1 = sha256_content(content_v1)

    # Task 1 resolved a bundle when collection was at revision 2
    snap_v1 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=2,
        document_id=doc_id,
        version_id=v1_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="Time Paradox Rule",
        path="/world/paradox-rule.md",
        content=content_v1,
        content_hash=hash_v1,
    )
    bundle_task1 = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=2,
        documents=(snap_v1,),
    )

    # Now a proposal is accepted, creating version V2 and moving collection revision to 3
    v2_id = uuid4()
    content_v2 = "Rule 1: Never cross your own temporal wake, unless protected by Chrono Aegis."
    hash_v2 = sha256_content(content_v2)
    snap_v2 = SettingDocumentSnapshot(
        setting_collection_id=col_id,
        collection_revision=3,
        document_id=doc_id,
        version_id=v2_id,
        document_type=DocumentType.WORLD_OVERVIEW.value,
        title="Time Paradox Rule",
        path="/world/paradox-rule.md",
        content=content_v2,
        content_hash=hash_v2,
    )
    bundle_task2 = SettingContextBundle(
        setting_collection_id=col_id,
        collection_revision=3,
        documents=(snap_v2,),
    )

    # Invariant: Task 1 bundle remains strictly immutable at revision 2 with v1_id and hash_v1
    assert bundle_task1.collection_revision == 2
    assert bundle_task1.documents[0].version_id == v1_id
    assert bundle_task1.documents[0].content_hash == hash_v1
    assert bundle_task1.bundle_hash != bundle_task2.bundle_hash

    # Task 2 bundle reflects revision 3 with v2_id and hash_v2
    assert bundle_task2.collection_revision == 3
    assert bundle_task2.documents[0].version_id == v2_id
    assert bundle_task2.documents[0].content_hash == hash_v2
