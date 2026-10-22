"""Only explicit setting archive actions create user-visible restore points."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.main import create_app
from app.models import DocumentSource, DocumentType, DocumentVersion
from app.services import DocumentService, SettingCollectionService
from app.services.document_service import (
    DocumentCommitIndeterminateError,
    DocumentVersionConflictError,
)
from app.services.setting_document_restore_points import SettingDocumentRestorePoints
from tests.integration.test_setting_history_archive import comment, setting

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_db_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value


async def version_count(session: AsyncSession, document_id):
    return await session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
    )


async def test_only_manual_actions_create_restore_points_without_creating_document_versions(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    original_id = doc.current_version_id
    latest = await DocumentService(async_session).write_document(
        document_id=doc.id,
        expected_current_version_id=original_id,
        content="autosaved current world",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
        setting_comments=[],
    )
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    before_revision = collection.revision
    assert (await client.get(path, headers=headers)).json() == []
    request = {"request_id": str(uuid4()), "expected_current_version_id": str(latest.id)}
    created = await client.post(path, json=request, headers=headers)
    assert created.status_code == 200, created.text
    point = created.json()
    assert point["id"] == request["request_id"]
    assert point["document_id"] == str(doc.id)
    assert point["version_id"] == str(latest.id)
    assert point["version"]["id"] == str(latest.id)
    assert point["point_number"] == 1
    assert point["summary"] == doc.title
    assert point["matches_current"] is True
    assert collection.revision == before_revision
    assert doc.current_version_id == latest.id
    assert await version_count(async_session, doc.id) == 2
    assert (await client.get(path, headers=headers)).json() == [point]
    assert (await client.post(path, json=request, headers=headers)).json() == point
    # Distinct deliberate user actions remain distinct, even on the same content.
    second = await client.post(path, json={**request, "request_id": str(uuid4())}, headers=headers)
    assert second.status_code == 200
    assert second.json()["point_number"] == 2
    assert len((await client.get(path, headers=headers)).json()) == 2
    assert await version_count(async_session, doc.id) == 2
    assert (
        await client.get(
            f"/api/v1/documents/{doc.id}/versions/{original_id}/content", headers=headers
        )
    ).json()["content"] == "old world"


async def test_manual_point_restore_recovers_its_feedback_and_precise_identity_without_auto_archiving(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    _, doc, owner, _ = await setting(async_session, tmp_path)
    service = DocumentService(async_session)
    old_comment = comment("old")
    target = await service.write_document(
        document_id=doc.id,
        content="old world",
        expected_current_version_id=doc.current_version_id,
        source=DocumentSource.USER,
        actor_user_id=owner.id,
        setting_comments=[old_comment],
    )
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    point = (
        await client.post(
            path,
            headers=headers,
            json={"request_id": str(uuid4()), "expected_current_version_id": str(target.id)},
        )
    ).json()
    latest = await service.write_document(
        document_id=doc.id,
        content="new world",
        expected_current_version_id=target.id,
        source=DocumentSource.USER,
        actor_user_id=owner.id,
        setting_comments=[comment("new")],
    )
    assert (await client.get(path, headers=headers)).json()[0]["matches_current"] is False
    assert (
        await client.patch(
            f"/api/v1/documents/{doc.id}", headers=headers, json={"title": "Keep this title"}
        )
    ).status_code == 200
    response = await client.post(
        f"{path}/{point['id']}/restore",
        headers=headers,
        json={"expected_current_version_id": str(latest.id)},
    )
    assert response.status_code == 200, response.text
    restored = response.json()
    assert restored["parent_version_id"] == str(latest.id)
    assert restored["restored_from_version_id"] == str(target.id)
    assert restored["restored_from_restore_point_id"] == point["id"]
    metadata = (await client.get(f"/api/v1/documents/{doc.id}", headers=headers)).json()
    assert metadata["title"] == "Keep this title"
    assert metadata["metadata"]["comments"] == [old_comment]
    assert metadata["metadata"]["comments_source_version_id"] == restored["id"]
    assert (await client.get(f"/api/v1/documents/{doc.id}/content", headers=headers)).json()[
        "content"
    ] == "old world"
    points = (await client.get(path, headers=headers)).json()
    assert len(points) == 1
    assert points[0]["matches_current"] is True
    repeated = await client.post(
        f"{path}/{point['id']}/restore",
        headers=headers,
        json={"expected_current_version_id": str(latest.id)},
    )
    assert repeated.status_code == 409
    assert await version_count(async_session, doc.id) == 4


async def test_manual_point_cas_and_request_identity_do_not_retarget_or_duplicate(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    _, doc, owner, _ = await setting(async_session, tmp_path)
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    first_id, request_id = doc.current_version_id, str(uuid4())
    point = await client.post(
        path,
        headers=headers,
        json={"request_id": request_id, "expected_current_version_id": str(first_id)},
    )
    assert point.status_code == 200, point.text
    latest = await DocumentService(async_session).write_document(
        document_id=doc.id,
        content="new world",
        expected_current_version_id=first_id,
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    # A lost creation response can still be recovered after a later autosave.
    same = await client.post(
        path,
        headers=headers,
        json={"request_id": request_id, "expected_current_version_id": str(first_id)},
    )
    assert same.status_code == 200
    assert same.json()["id"] == point.json()["id"]
    assert same.json()["matches_current"] is False
    assert (
        await client.post(
            path,
            headers=headers,
            json={"request_id": request_id, "expected_current_version_id": str(latest.id)},
        )
    ).status_code == 409
    assert (
        await client.post(
            path,
            headers=headers,
            json={"request_id": str(uuid4()), "expected_current_version_id": str(first_id)},
        )
    ).status_code == 409
    assert len((await client.get(path, headers=headers)).json()) == 1


async def test_restore_points_cannot_be_rebound_or_restored_through_another_document(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    request = {
        "request_id": str(uuid4()),
        "expected_current_version_id": str(doc.current_version_id),
    }
    created = await client.post(path, json=request, headers=headers)
    assert created.status_code == 200, created.text
    other = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id,
        document_type=DocumentType.WORLD_OVERVIEW,
        title="Other",
        path="world/other-point.md",
        content="other world",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
    )
    other_path = f"/api/v1/documents/{other.id}/restore-points"
    assert (
        await client.post(
            other_path,
            json={**request, "expected_current_version_id": str(other.current_version_id)},
            headers=headers,
        )
    ).status_code == 409
    assert (
        await client.post(
            f"{other_path}/{created.json()['id']}/restore",
            headers=headers,
            json={"expected_current_version_id": str(other.current_version_id)},
        )
    ).status_code == 404
    assert (await client.get(other_path, headers=headers)).json() == []


@pytest.mark.parametrize("state", ["foreign", "archived", "deleted"])
async def test_manual_point_routes_preserve_owner_archive_and_deletion_boundaries(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    state: str,
):
    collection, doc, owner, other = await setting(async_session, tmp_path)
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    request = {
        "request_id": str(uuid4()),
        "expected_current_version_id": str(doc.current_version_id),
    }
    created = await client.post(path, json=request, headers=headers)
    assert created.status_code == 200, created.text
    if state == "foreign":
        headers = {"x-actor-user-id": str(other.id)}
        expected = 403
    elif state == "archived":
        await SettingCollectionService(async_session).archive_setting_collection(
            collection.id, actor_user_id=owner.id
        )
        expected = 409
    else:
        assert (
            await client.delete(f"/api/v1/documents/{doc.id}", headers=headers)
        ).status_code == 204
        expected = 404
    listed = await client.get(path, headers=headers)
    assert listed.status_code == (200 if state == "archived" else expected)
    assert (
        await client.post(path, json={**request, "request_id": str(uuid4())}, headers=headers)
    ).status_code == expected
    assert (
        await client.post(
            f"{path}/{created.json()['id']}/restore",
            headers=headers,
            json={
                "expected_current_version_id": request["expected_current_version_id"],
                "actor_user_id": str(owner.id),
            },
        )
    ).status_code == expected


@pytest.mark.parametrize("field", ["request_id", "expected_current_version_id"])
async def test_manual_point_rejects_nil_identities_without_creating_a_point(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    field: str,
):
    _, doc, _, _ = await setting(async_session, tmp_path)
    request = {
        "request_id": str(uuid4()),
        "expected_current_version_id": str(doc.current_version_id),
    }
    request[field] = "00000000-0000-0000-0000-000000000000"
    assert (
        await client.post(f"/api/v1/documents/{doc.id}/restore-points", json=request)
    ).status_code == 422


@pytest.mark.parametrize("bound_to_old_version", [False, True])
async def test_v1_metadata_feedback_is_archived_but_explicitly_stale_feedback_is_not(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    bound_to_old_version: bool,
):
    collection, _, owner, _ = await setting(async_session, tmp_path)
    old_comment = comment("old")
    metadata = {"comments": [old_comment]}
    if bound_to_old_version:
        metadata["comments_source_version_id"] = str(uuid4())
    service = DocumentService(async_session)
    doc = await service.create_document(
        setting_collection_id=collection.id,
        document_type=DocumentType.CHARACTER_PROFILE,
        title="V1 feedback",
        path="setting/v1.md",
        content="old world",
        source=DocumentSource.USER,
        actor_user_id=owner.id,
        metadata=metadata,
    )
    path, headers = f"/api/v1/documents/{doc.id}/restore-points", {"x-actor-user-id": str(owner.id)}
    target_id = doc.current_version_id
    response = await client.post(
        path,
        headers=headers,
        json={"request_id": str(uuid4()), "expected_current_version_id": str(target_id)},
    )
    assert response.status_code == 200, response.text
    assert await version_count(async_session, doc.id) == 1
    point = response.json()
    latest = await service.write_document(
        document_id=doc.id,
        content="new world",
        expected_current_version_id=target_id,
        source=DocumentSource.USER,
        actor_user_id=owner.id,
        setting_comments=[],
    )
    restored = await client.post(
        f"{path}/{point['id']}/restore",
        headers=headers,
        json={"expected_current_version_id": str(latest.id)},
    )
    assert restored.status_code == 200, restored.text
    current = (await client.get(f"/api/v1/documents/{doc.id}", headers=headers)).json()
    assert current["metadata"]["comments"] == ([] if bound_to_old_version else [old_comment])
    assert current["metadata"]["comments_source_version_id"] == restored.json()["id"]


async def test_manual_archive_rejects_invalid_saved_anchors_and_client_snapshot_injection(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
):
    _, doc, _, _ = await setting(async_session, tmp_path)
    doc.metadata_ = {"comments": [comment("wrong quote")]}
    await async_session.commit()
    path = f"/api/v1/documents/{doc.id}/restore-points"
    request = {
        "request_id": str(uuid4()),
        "expected_current_version_id": str(doc.current_version_id),
    }
    assert (await client.post(path, json=request)).status_code == 409
    assert (await client.get(path)).json() == []
    assert (
        await client.post(
            path, json={**request, "comments_snapshot": [], "version_id": str(uuid4())}
        )
    ).status_code == 422
    assert await version_count(async_session, doc.id) == 1


@pytest.mark.parametrize("operation", ["create", "restore"])
async def test_manual_archive_commit_ack_loss_reconciles_by_request_and_restore_point_identity(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    _, doc, owner, _ = await setting(async_session, tmp_path)
    document_id, actor_id, target_id, request_id = doc.id, owner.id, doc.current_version_id, uuid4()
    before_id = target_id
    if operation == "restore":
        await SettingDocumentRestorePoints(async_session).create(
            document_id,
            request_id=request_id,
            expected_current_version_id=target_id,
            actor_user_id=actor_id,
        )
        latest = await DocumentService(async_session).write_document(
            document_id=document_id,
            expected_current_version_id=target_id,
            content="new world",
            source=DocumentSource.USER,
            actor_user_id=actor_id,
        )
        before_id = latest.id
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    async with factory() as writer:
        original = writer.commit

        async def lose_ack():
            await original()
            raise ConnectionError("Commit acknowledgement lost")

        monkeypatch.setattr(writer, "commit", lose_ack)
        with pytest.raises(
            ConnectionError if operation == "create" else DocumentCommitIndeterminateError
        ):
            if operation == "create":
                await SettingDocumentRestorePoints(writer).create(
                    document_id,
                    request_id=request_id,
                    expected_current_version_id=target_id,
                    actor_user_id=actor_id,
                )
            else:
                await SettingDocumentRestorePoints(writer).restore(
                    document_id,
                    request_id,
                    expected_current_version_id=before_id,
                    actor_user_id=actor_id,
                )
    async with factory() as reader:
        points = await SettingDocumentRestorePoints(reader).list(
            document_id, actor_user_id=actor_id
        )
        assert len(points) == 1 and points[0]["id"] == request_id
        if operation == "create":
            repeated = await SettingDocumentRestorePoints(reader).create(
                document_id,
                request_id=request_id,
                expected_current_version_id=target_id,
                actor_user_id=actor_id,
            )
            assert repeated["id"] == request_id
            assert await version_count(reader, document_id) == 1
        else:
            current = await DocumentService(reader).read_current_content(document_id)
            version = await reader.get(DocumentVersion, current.version_id)
            assert version.parent_version_id == before_id
            assert version.restored_from_version_id == str(target_id)
            assert version.restored_from_restore_point_id == str(request_id)
            assert current.content == "old world"
            with pytest.raises(DocumentVersionConflictError):
                await SettingDocumentRestorePoints(reader).restore(
                    document_id,
                    request_id,
                    expected_current_version_id=before_id,
                    actor_user_id=actor_id,
                )
            assert await version_count(reader, document_id) == 3


@pytest.mark.parametrize("same_request", [True, False])
async def test_concurrent_manual_actions_have_stable_ids_and_independent_point_numbers(
    async_session: AsyncSession,
    tmp_path: Path,
    same_request: bool,
):
    collection, doc, owner, _ = await setting(async_session, tmp_path)
    document_id, actor_id, version_id, initial_revision = (
        doc.id,
        owner.id,
        doc.current_version_id,
        collection.revision,
    )
    first_id = uuid4()
    requests = [first_id, first_id if same_request else uuid4()]
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def create(request_id):
        async with factory() as writer:
            return await SettingDocumentRestorePoints(writer).create(
                document_id,
                request_id=request_id,
                expected_current_version_id=version_id,
                actor_user_id=actor_id,
            )

    results = await asyncio.gather(*(create(request_id) for request_id in requests))
    assert {item["id"] for item in results} == set(requests)
    assert {item["point_number"] for item in results} == ({1} if same_request else {1, 2})
    async with factory() as reader:
        points = await SettingDocumentRestorePoints(reader).list(
            document_id, actor_user_id=actor_id
        )
        assert len(points) == (1 if same_request else 2)
        assert await version_count(reader, document_id) == 1
        saved_collection = await SettingCollectionService(reader).get_setting_collection(
            collection.id
        )
        assert saved_collection.revision == initial_revision
