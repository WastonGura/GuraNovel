"""Setting history restores append safely and serialize with collection archival."""
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db_session
from app.core.errors import ConflictError
from app.main import create_app
from app.models import DocumentSource, DocumentType, DocumentVersion, SettingCollection, User
from app.services import DocumentService, SettingCollectionService
from app.services.document_service import DocumentCommitIndeterminateError, DocumentVersionConflictError

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as value:
        yield value


async def setting(async_session: AsyncSession, tmp_path: Path):
    owner, other = [User(username=f"history-{uuid4().hex}", display_name="History") for _ in range(2)]
    async_session.add_all([owner, other])
    await async_session.flush()
    collection = SettingCollection(slug=f"history-{uuid4().hex}", title="History",
        workspace_root=str(tmp_path), owner_id=owner.id, status="active", revision=1)
    async_session.add(collection)
    await async_session.commit()
    doc = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="World", path="world/history.md", content="old world", source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    return collection, doc, owner, other


def comment(quote: str) -> dict:
    return {"id": str(uuid4()), "start": 0, "end": len(quote), "quote": quote,
            "text": "Historical feedback", "color": "#8d9bff", "submitted": False, "orphaned": False}


@pytest.mark.parametrize("historical_comments", [False, True])
async def test_history_restore_rebinds_feedback_and_records_reconciliation_identity(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path, historical_comments: bool,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    service = DocumentService(async_session)
    target_id = doc.current_version_id
    old_comments = [comment("old")] if historical_comments else []
    if historical_comments:
        target = await service.write_document(document_id=doc.id, content="old world",
            expected_current_version_id=target_id, source=DocumentSource.USER,
            actor_user_id=owner.id, setting_comments=old_comments)
        target_id = target.id
    latest = await service.write_document(document_id=doc.id, content="new world",
        expected_current_version_id=target_id, source=DocumentSource.USER,
        actor_user_id=owner.id, setting_comments=[comment("new")])
    url = f"/api/v1/documents/{doc.id}"
    headers = {"x-actor-user-id": str(owner.id)}
    restored = await client.post(f"{url}/versions/{target_id}/restore", headers=headers,
        json={"expected_current_version_id": str(latest.id), "change_summary": "Restore history"})
    assert restored.status_code == 200, restored.text
    version = restored.json()
    assert version["id"] not in (str(target_id), str(latest.id))
    assert version["parent_version_id"] == str(latest.id)
    assert version["restored_from_version_id"] == str(target_id)
    metadata = (await client.get(url, headers=headers)).json()["metadata"]
    assert metadata["comments"] == old_comments
    assert metadata["comments_source_version_id"] == version["id"]
    stored = await async_session.get(DocumentVersion, UUID(version["id"]))
    assert stored.metadata_["setting_comments"] == old_comments
    assert (await client.get(f"{url}/content", headers=headers)).json()["content"] == "old world"
    history = (await client.get(f"{url}/versions", headers=headers)).json()
    assert history[-1]["restored_from_version_id"] == str(target_id)
    # A lost response is reconciled by this exact child+target identity, never a second write.
    repeated = await client.post(f"{url}/versions/{target_id}/restore", headers=headers,
        json={"expected_current_version_id": str(latest.id)})
    assert repeated.status_code == 409
    assert len((await client.get(f"{url}/versions", headers=headers)).json()) == len(history)
    assert (await client.get(f"{url}/versions/{latest.id}/content", headers=headers)).json()["content"] == "new world"


async def test_history_routes_reject_foreign_owner_and_deleted_documents(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    _, doc, owner, other = await setting(async_session, tmp_path)
    url, version_id = f"/api/v1/documents/{doc.id}", doc.current_version_id
    other_headers = {"x-actor-user-id": str(other.id)}
    for path in (url, f"{url}/content", f"{url}/versions", f"{url}/versions/{version_id}/content"):
        assert (await client.get(path, headers=other_headers)).status_code == 403
    assert (await client.post(f"{url}/versions/{version_id}/restore", headers=other_headers,
        json={"expected_current_version_id": str(version_id)})).status_code == 403
    # A request cannot replace its explicit actor with an owner identity in the payload.
    assert (await client.post(f"{url}/versions/{version_id}/restore", headers=other_headers,
        json={"expected_current_version_id": str(version_id), "actor_user_id": str(owner.id)})).status_code == 403
    assert (await client.put(f"{url}/content", headers=other_headers,
        json={"expected_current_version_id": str(version_id), "actor_user_id": str(owner.id),
              "content": "spoofed"})).status_code == 403
    headers = {"x-actor-user-id": str(owner.id)}
    assert (await client.delete(url, headers=headers)).status_code == 204
    for path in (f"{url}/versions", f"{url}/versions/{version_id}/content"):
        assert (await client.get(path, headers=headers)).status_code == 404
    assert (await client.post(f"{url}/versions/{version_id}/restore", headers=headers,
        json={"expected_current_version_id": str(version_id)})).status_code == 404


async def test_restore_rejects_stale_or_foreign_target_without_modifying_history(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    service = DocumentService(async_session)
    first_id = doc.current_version_id
    latest = await service.write_document(document_id=doc.id, content="new world",
        expected_current_version_id=first_id, source=DocumentSource.USER, actor_user_id=owner.id)
    other = await service.create_document(setting_collection_id=collection.id,
        document_type=DocumentType.WORLD_OVERVIEW, title="Other", path="world/other.md",
        content="other world", source=DocumentSource.USER, actor_user_id=owner.id)
    before_revision = collection.revision
    url, headers = f"/api/v1/documents/{doc.id}", {"x-actor-user-id": str(owner.id)}
    stale = await client.post(f"{url}/versions/{first_id}/restore", headers=headers,
        json={"expected_current_version_id": str(first_id)})
    assert stale.status_code == 409
    foreign = await client.post(f"{url}/versions/{other.current_version_id}/restore", headers=headers,
        json={"expected_current_version_id": str(latest.id)})
    assert foreign.status_code == 404
    assert collection.revision == before_revision
    assert (await client.get(f"{url}/content", headers=headers)).json() == {
        "document_id": str(doc.id), "version_id": str(latest.id), "content": "new world"}
    history = (await client.get(f"{url}/versions", headers=headers)).json()
    assert len(history) == 2
    assert all(version["restored_from_version_id"] is None for version in history)


@pytest.mark.parametrize("operation", ["archive", "restore"])
async def test_lost_commit_acknowledgement_can_be_reconciled_without_a_second_mutation(
    async_session: AsyncSession, tmp_path: Path, integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch, operation: str,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    collection_id, doc_id, owner_id, target_id = collection.id, doc.id, owner.id, doc.current_version_id
    before_id = target_id
    if operation == "restore":
        latest = await DocumentService(async_session).write_document(document_id=doc_id, content="new world",
            expected_current_version_id=target_id, source=DocumentSource.USER, actor_user_id=owner_id)
        before_id = latest.id
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as writer:
            original = writer.commit

            async def lose_acknowledgement():
                await original()
                raise ConnectionError("Commit acknowledgement lost")

            with monkeypatch.context() as patch:
                patch.setattr(writer, "commit", lose_acknowledgement)
                with pytest.raises(ConnectionError if operation == "archive" else DocumentCommitIndeterminateError):
                    if operation == "archive":
                        await SettingCollectionService(writer).archive_setting_collection(collection_id, actor_user_id=owner_id)
                    else:
                        await DocumentService(writer).restore_document(document_id=doc_id, version_id=target_id,
                            expected_current_version_id=before_id, source=DocumentSource.USER, actor_user_id=owner_id)
        async with factory() as reader:
            if operation == "archive":
                reconciled = await SettingCollectionService(reader).get_setting_collection(collection_id, actor_user_id=owner_id)
                assert reconciled.status == "archived"
                revision = reconciled.revision
                repeated = await SettingCollectionService(reader).archive_setting_collection(collection_id, actor_user_id=owner_id)
                assert repeated.revision == revision
            else:
                current = await DocumentService(reader).read_current_content(doc_id)
                version = await reader.get(DocumentVersion, current.version_id)
                assert version.parent_version_id == before_id
                assert version.restored_from_version_id == str(target_id)
                assert current.content == "old world"
                with pytest.raises(DocumentVersionConflictError):
                    await DocumentService(reader).restore_document(document_id=doc_id, version_id=target_id,
                        expected_current_version_id=before_id, source=DocumentSource.USER, actor_user_id=owner_id)
                versions = list(await reader.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc_id)))
                assert len(versions) == 3
    finally:
        await engine.dispose()


@pytest.mark.parametrize("operation", ["write", "restore", "create"])
async def test_archival_refreshes_a_previously_loaded_collection_before_mutating(
    async_session: AsyncSession, tmp_path: Path, integration_database_url: str, operation: str,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    collection_id, doc_id, owner_id, version_id = collection.id, doc.id, owner.id, doc.current_version_id
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as writer, factory() as archiver:
            cached = await writer.get(SettingCollection, collection_id)
            assert cached.status == "active"
            archived = await SettingCollectionService(archiver).archive_setting_collection(collection_id, actor_user_id=owner_id)
            assert archived.status == "archived"
            with pytest.raises(ConflictError, match="Archived"):
                if operation == "create":
                    await DocumentService(writer).create_document(setting_collection_id=collection_id,
                        document_type=DocumentType.WORLD_OVERVIEW, title="Must not exist", path="world/late.md",
                        content="late", source=DocumentSource.USER, actor_user_id=owner_id)
                elif operation == "restore":
                    await DocumentService(writer).restore_document(document_id=doc_id, version_id=version_id,
                        expected_current_version_id=version_id, source=DocumentSource.USER, actor_user_id=owner_id)
                else:
                    await DocumentService(writer).write_document(document_id=doc_id, content="late",
                        expected_current_version_id=version_id, source=DocumentSource.USER, actor_user_id=owner_id)
            await writer.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("operation", ["write", "restore"])
async def test_archive_waits_for_inflight_version_and_is_idempotent(
    async_session: AsyncSession, tmp_path: Path, integration_database_url: str,
    monkeypatch: pytest.MonkeyPatch, operation: str,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    collection_id, doc_id, owner_id, version_id = collection.id, doc.id, owner.id, doc.current_version_id
    initial_revision = collection.revision
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ready, release = asyncio.Event(), asyncio.Event()
    try:
        async with factory() as writer, factory() as archiver:
            service = DocumentService(writer)
            original = service._next_version_number

            async def paused_number(*args):
                ready.set()
                await release.wait()
                return await original(*args)

            monkeypatch.setattr(service, "_next_version_number", paused_number)
            arguments = dict(document_id=doc_id, expected_current_version_id=version_id,
                             source=DocumentSource.USER, actor_user_id=owner_id)
            writing = asyncio.create_task(service.restore_document(version_id=version_id, **arguments)
                if operation == "restore" else service.write_document(content="saved before archive", **arguments))
            await asyncio.wait_for(ready.wait(), timeout=3)
            archiving = asyncio.create_task(SettingCollectionService(archiver).archive_setting_collection(collection_id, actor_user_id=owner_id))
            try:
                await asyncio.sleep(0.1)
                assert not archiving.done(), "Archive raced past an uncommitted document write"
            finally:
                release.set()
                await writing
                await archiving
            archived = await SettingCollectionService(archiver).archive_setting_collection(collection_id, actor_user_id=owner_id)
            assert archived.status == "archived"
            assert archived.revision == initial_revision + 2  # one version and one archive transition
            updated_at = archived.updated_at
            repeated = await SettingCollectionService(archiver).archive_setting_collection(collection_id, actor_user_id=owner_id)
            assert repeated.revision == archived.revision
            assert repeated.updated_at == updated_at
            versions = list(await writer.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc_id)))
            assert len(versions) == 2
    finally:
        await engine.dispose()
