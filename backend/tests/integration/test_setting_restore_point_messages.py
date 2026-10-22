"""A manual archive's optional explanation changes independently from saved prose."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.core.errors import ConflictError
from app.main import create_app
from app.models import DocumentSource, DocumentType, DocumentVersion, SettingDocumentRestorePoint
from app.services import DocumentService, SettingCollectionService
from app.services.setting_document_restore_points import SettingDocumentRestorePoints
from tests.integration.test_setting_history_archive import comment, setting

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value


async def seed(client, session, tmp_path):
    collection, doc, owner, other = await setting(session, tmp_path)
    doc.metadata_ = {"comments": [comment("old")]}
    await session.commit()
    headers = {"x-actor-user-id": str(owner.id)}
    path = f"/api/v1/documents/{doc.id}/restore-points"
    created = await client.post(
        path,
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "expected_current_version_id": str(doc.current_version_id),
        },
    )
    assert created.status_code == 200, created.text
    return collection, doc, owner, other, headers, path, created.json()


async def test_message_patch_roundtrips_normalizes_and_preserves_the_archived_version(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    collection, doc, _, _, headers, path, point = await seed(client, async_session, tmp_path)
    assert point["message"] is None
    current = (await client.get(f"/api/v1/documents/{doc.id}", headers=headers)).json()
    content = (await client.get(f"/api/v1/documents/{doc.id}/content", headers=headers)).json()
    revision = collection.revision
    collection_id, document_id = collection.id, doc.id
    response = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "  补齐潮汐规则  ", "expected_message": None},
    )
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed == {**point, "message": "补齐潮汐规则"}
    assert (await client.get(path, headers=headers)).json() == [changed]
    assert (await client.get(f"/api/v1/documents/{doc.id}", headers=headers)).json() == current
    assert (
        await client.get(f"/api/v1/documents/{doc.id}/content", headers=headers)
    ).json() == content
    async_session.expire_all()
    assert (await async_session.get(type(collection), collection_id)).revision == revision
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(SettingDocumentRestorePoint)
            .where(SettingDocumentRestorePoint.document_id == document_id)
        )
        == 1
    )
    # Repeating an already-applied desired value confirms success even with the old expected value.
    repeated = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "补齐潮汐规则", "expected_message": None},
    )
    assert repeated.status_code == 200
    assert repeated.json() == changed
    stale = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "不得覆盖", "expected_message": None},
    )
    assert stale.status_code == 409
    assert (await client.get(path, headers=headers)).json() == [changed]
    cleared = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "  ", "expected_message": "  补齐潮汐规则  "},
    )
    assert cleared.status_code == 200
    assert cleared.json() == point
    assert (await client.get(path, headers=headers)).json() == [point]


@pytest.mark.parametrize("state", ["foreign", "archived", "deleted", "other-document"])
async def test_message_patch_preserves_owner_archive_deletion_and_point_scope(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    state: str,
):
    collection, doc, owner, other, headers, path, point = await seed(
        client, async_session, tmp_path
    )
    if state == "foreign":
        headers = {"x-actor-user-id": str(other.id)}
        expected = 403
    elif state == "archived":
        await SettingCollectionService(async_session).archive_setting_collection(
            collection.id, actor_user_id=owner.id
        )
        expected = 409
    elif state == "deleted":
        assert (
            await client.delete(f"/api/v1/documents/{doc.id}", headers=headers)
        ).status_code == 204
        expected = 404
    else:
        other_doc = await DocumentService(async_session).create_document(
            setting_collection_id=collection.id,
            document_type=DocumentType.WORLD_OVERVIEW,
            title="Other",
            path="world/other-message.md",
            content="other",
            source=DocumentSource.USER,
            actor_user_id=owner.id,
        )
        path = f"/api/v1/documents/{other_doc.id}/restore-points"
        expected = 404
    response = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "Must not change", "expected_message": None},
    )
    assert response.status_code == expected, response.text
    async_session.expire_all()
    assert (await async_session.get(SettingDocumentRestorePoint, UUID(point["id"]))).message is None


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "Missing expected"},
        {"expected_message": None},
        {"message": "x", "expected_message": None, "summary": "Do not rename"},
        {"message": "x" * 161, "expected_message": None},
        {"message": "first\nsecond", "expected_message": None},
        {"message": "bad\x00text", "expected_message": None},
        {"message": 42, "expected_message": None},
    ],
)
async def test_message_patch_rejects_invalid_or_incomplete_requests(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    payload: dict,
):
    _, _, _, _, headers, path, point = await seed(client, async_session, tmp_path)
    response = await client.patch(f"{path}/{point['id']}/message", headers=headers, json=payload)
    assert response.status_code == 422
    assert (await client.get(path, headers=headers)).json() == [point]


async def test_concurrent_message_edits_compare_their_captured_base(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    _, _, _, _, headers, path, point = await seed(client, async_session, tmp_path)
    results = await asyncio.gather(
        *(
            client.patch(
                f"{path}/{point['id']}/message",
                headers=headers,
                json={"message": target, "expected_message": None},
            )
            for target in ("First", "Second")
        )
    )
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert (await client.get(path, headers=headers)).json() == [winner]


async def test_lost_message_commit_ack_can_be_confirmed_without_changing_the_point_again(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, doc, owner, _, headers, path, point = await seed(client, async_session, tmp_path)
    document_id, point_id, actor_id = doc.id, UUID(point["id"]), owner.id
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    async with factory() as writer:
        original = writer.commit

        async def lose_ack():
            await original()
            raise ConnectionError("Commit acknowledgement lost")

        monkeypatch.setattr(writer, "commit", lose_ack)
        with pytest.raises(ConnectionError):
            await SettingDocumentRestorePoints(writer).update_message(
                document_id,
                point_id,
                message="Confirmed message",
                expected_message=None,
                actor_user_id=actor_id,
            )
    assert (await client.get(path, headers=headers)).json() == [
        {**point, "message": "Confirmed message"}
    ]
    repeated = await client.patch(
        f"{path}/{point['id']}/message",
        headers=headers,
        json={"message": "Confirmed message", "expected_message": None},
    )
    assert repeated.status_code == 200
    assert repeated.json() == {**point, "message": "Confirmed message"}


async def test_message_update_refreshes_cached_collection_state_after_another_session_archives(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    collection, doc, owner, _, _, _, point = await seed(client, async_session, tmp_path)
    collection_id, document_id, owner_id, point_id = (
        collection.id,
        doc.id,
        owner.id,
        UUID(point["id"]),
    )
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    async with factory() as writer, factory() as archiver:
        cached = await writer.get(type(collection), collection_id)
        assert cached.status == "active"
        await SettingCollectionService(archiver).archive_setting_collection(
            collection_id, actor_user_id=owner_id
        )
        with pytest.raises(ConflictError, match="Archived"):
            await SettingDocumentRestorePoints(writer).update_message(
                document_id,
                point_id,
                message="Must not change",
                expected_message=None,
                actor_user_id=owner_id,
            )
        await writer.rollback()
    async with factory() as reader:
        assert (await reader.get(SettingDocumentRestorePoint, point_id)).message is None
