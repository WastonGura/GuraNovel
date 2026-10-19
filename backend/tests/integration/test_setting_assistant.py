"""Collection-scoped Gura persists without borrowing any novel identity."""
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_db_session
from app.main import create_app
from app.models import DocumentSource, DocumentType, SettingCollection, User
from app.services.document_service import DocumentService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def collection_client(async_session):
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def seed_collection(session, tmp_path):
    collection = SettingCollection(title="No novel world", slug=str(uuid4()), workspace_root=str(tmp_path))
    session.add(collection)
    await session.commit()
    return collection


async def test_collection_conversation_persists_and_rejects_another_collection(collection_client, async_session, tmp_path):
    collection = await seed_collection(async_session, tmp_path)
    other = await seed_collection(async_session, tmp_path)
    path = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
    response = await collection_client.post(path, json={})
    assert response.status_code == 200, response.text
    conversation = response.json()
    assert conversation["project_id"] is None
    assert conversation["setting_collection_id"] == str(collection.id)
    assert (await collection_client.post(path, json={})).json()["id"] == conversation["id"]
    assert (await collection_client.get(f"{path}/{conversation['id']}")).json()["id"] == conversation["id"]
    cross_scope = f"/api/v1/setting-collections/{other.id}/assistant/conversations/{conversation['id']}"
    assert (await collection_client.get(cross_scope)).status_code == 404


async def test_saved_document_context_idempotency_and_read_only_reply(collection_client, async_session, tmp_path, monkeypatch):
    collection = await seed_collection(async_session, tmp_path)
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="Tide world", path="world.md", content="The tide reverses at midnight.", source=DocumentSource.USER,
    )
    captured = []

    async def reply(content, context, history):
        captured.append(context)
        return "Read-only response"

    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", reply)
    path = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
    conversation = (await collection_client.post(path)).json()
    url = f"{path}/{conversation['id']}/messages"
    payload = {"content": "Explain the tide", "client_message_id": "same-request", "document_id": str(document.id), "document_version_id": str(document.current_version_id)}
    response = await collection_client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert [m["role"] for m in response.json()["messages"]] == ["user", "assistant"]
    assert captured[0]["body"] == "The tide reverses at midnight."
    assert captured[0]["document_version_id"] == str(document.current_version_id)
    assert captured[0]["collection_id"] == str(collection.id)
    assert (await collection_client.post(url, json=payload)).json()["messages"] == response.json()["messages"]
    assert len(captured) == 1
    changed = await collection_client.post(url, json={**payload, "content": "Different text"})
    assert changed.status_code == 409
    changed_context = await collection_client.post(url, json={**payload, "document_version_id": str(uuid4())})
    assert changed_context.status_code == 409
    stale = await collection_client.post(url, json={**payload, "client_message_id": "new", "document_version_id": str(uuid4())})
    assert stale.status_code == 409
    wrong = await collection_client.post(url, json={**payload, "client_message_id": "wrong", "document_id": str(uuid4())})
    assert wrong.status_code == 404
    assert len((await collection_client.get(f"{path}/{conversation['id']}")).json()["messages"]) == 2
    assert (await DocumentService(async_session).read_current_content(document.id)).content == captured[0]["body"]


async def test_owner_checks_and_failed_model_calls_leave_no_fake_messages(collection_client, async_session, tmp_path, monkeypatch):
    from app.llm.errors import ProviderUnavailableError
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    collection = await seed_collection(async_session, tmp_path)
    collection.owner_id = owner.id
    await async_session.commit()
    path = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
    assert (await collection_client.post(path, headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
    conversation = (await collection_client.post(path, headers={"x-actor-user-id": str(owner.id)})).json()
    assert (await collection_client.get(f"{path}/{conversation['id']}", headers={"x-actor-user-id": str(stranger.id)})).status_code == 403

    async def fail(*args):
        raise ProviderUnavailableError()

    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", fail)
    url = f"{path}/{conversation['id']}/messages"
    payload = {"content": "How do I go home?", "client_message_id": "retry-me"}
    assert (await collection_client.post(url, json=payload)).status_code == 503
    assert (await collection_client.get(f"{path}/{conversation['id']}")).json()["messages"] == []

    async def recovered(*args):
        return "Use the home icon."

    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", recovered)
    assert len((await collection_client.post(url, json=payload)).json()["messages"]) == 2


async def test_parallel_creation_and_duplicate_delivery_do_not_duplicate_messages(collection_client, async_session, tmp_path, monkeypatch):
    import asyncio
    collection = await seed_collection(async_session, tmp_path)
    path = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
    first, second = await asyncio.gather(collection_client.post(path), collection_client.post(path))
    assert first.json()["id"] == second.json()["id"]
    calls = []

    async def reply(*args):
        calls.append(1)
        return "One reply."

    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", reply)
    url = f"{path}/{first.json()['id']}/messages"
    payload = {"content": "A single request", "client_message_id": "duplicate"}
    results = await asyncio.gather(collection_client.post(url, json=payload), collection_client.post(url, json=payload))
    assert [r.status_code for r in results] == [200, 200]
    assert len(calls) == 1
    assert all(len(r.json()["messages"]) == 2 for r in results)


async def test_archived_and_unlinked_collections_keep_independent_read_only_context(collection_client, async_session, tmp_path, monkeypatch):
    active = await seed_collection(async_session, tmp_path)
    archived = await seed_collection(async_session, tmp_path)
    archived.status = "archived"
    await async_session.commit()
    captured = []

    async def reply(content, context, history):
        captured.append((context["collection_id"], context["status"], history))
        return f"Read {context['status']} collection"

    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", reply)
    for collection in [active, archived]:
        path = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
        conversation = (await collection_client.post(path)).json()
        response = await collection_client.post(f"{path}/{conversation['id']}/messages", json={"content": "Explain this collection", "client_message_id": "same-id-in-separate-contexts"})
        assert response.status_code == 200, response.text
        assert len(response.json()["messages"]) == 2
    assert captured == [(str(active.id), "active", []), (str(archived.id), "archived", [])]
    await async_session.refresh(archived)
    assert archived.status == "archived"
