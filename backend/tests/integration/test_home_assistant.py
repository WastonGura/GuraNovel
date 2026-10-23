"""Real PostgreSQL persistence and isolation for server-side home guidance."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.api.schemas_studio_assistant import HomeAssistantSendRequest
from app.main import create_app
from app.models import Chapter, Project, SettingCollection, StudioAssistantConversation, StudioAssistantMessage
from app.services.studio_assistant_service import StudioAssistantService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
BASE = "/api/v1/assistant/conversations"


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_home_persists_messages_and_replays_lost_ack_without_model(client, monkeypatch):
    from app.agents.studio_assistant import StudioAssistantAgent
    from app.agents import setting_assistant

    forbidden = AsyncMock(side_effect=AssertionError("Home must not invoke a model or project agent"))
    monkeypatch.setattr(StudioAssistantAgent, "generate_reply", forbidden)
    monkeypatch.setattr(setting_assistant, "generate_setting_reply", forbidden)
    created = await client.post(BASE)
    assert created.status_code == 200, created.text
    conv = created.json()
    assert conv["project_id"] is conv["setting_collection_id"] is conv["chapter_id"] is None
    assert "home_owner_key" not in conv
    assert (await client.post(BASE)).json()["id"] == conv["id"]

    payload = {"content": "如何创建作品？", "client_message_id": "lost-ack", "current_view": "home"}
    first = await client.post(f"{BASE}/{conv['id']}/messages", json=payload)
    assert first.status_code == 200, first.text
    assert [message["role"] for message in first.json()["messages"]] == ["user", "assistant"]
    assert first.json()["messages"][0]["tool_calls"] == [{
        "client_message_id": "lost-ack", "context": {"current_view": "home"},
    }]
    assert "新建作品" in first.json()["messages"][-1]["content"]
    assert first.json()["messages"][-1]["tool_calls"] is None

    # A new HTTP request/session simulates reload after the committed ACK was lost.
    replay = await client.post(f"{BASE}/{conv['id']}/messages", json=payload)
    loaded = await client.get(f"{BASE}/{conv['id']}")
    assert replay.json() == loaded.json() == first.json()
    assert [item["id"] for item in (await client.get(BASE)).json()] == [conv["id"]]
    conflict = await client.post(f"{BASE}/{conv['id']}/messages", json=payload | {"content": "其他问题"})
    assert conflict.status_code == 409
    assert forbidden.await_count == 0


async def test_home_actor_and_scope_isolation(client, async_session, tmp_path):
    actor, other = uuid4(), uuid4()
    headers = {"x-actor-user-id": str(actor)}
    home = (await client.post(BASE, headers=headers)).json()
    local = (await client.post(BASE)).json()
    assert home["id"] != local["id"]
    assert (await client.get(BASE, headers={"x-actor-user-id": str(other)})).json() == []
    for candidate in ({}, {"x-actor-user-id": str(other)}):
        assert (await client.get(f"{BASE}/{home['id']}", headers=candidate)).status_code == 404
        assert (await client.post(f"{BASE}/{home['id']}/messages", headers=candidate, json={
            "content": "help", "client_message_id": "foreign",
        })).status_code == 404

    project = Project(slug=str(uuid4()), title="Private project", workspace_root=str(tmp_path))
    collection = SettingCollection(slug=str(uuid4()), title="World", workspace_root=str(tmp_path / "world"))
    async_session.add_all([project, collection])
    await async_session.commit()
    project_base = f"/api/v1/projects/{project.id}/assistant/conversations"
    collection_base = f"/api/v1/setting-collections/{collection.id}/assistant/conversations"
    project_conv = (await client.post(project_base)).json()
    collection_conv = (await client.post(collection_base)).json()
    for foreign in (project_conv, collection_conv):
        assert (await client.get(f"{BASE}/{foreign['id']}", headers=headers)).status_code == 404
    assert (await client.get(f"{project_base}/{home['id']}")).status_code == 404
    assert (await client.get(f"{collection_base}/{home['id']}")).status_code == 404


@pytest.mark.parametrize("payload", [
    {"content": "help"},
    {"content": "help", "client_message_id": ""},
    {"content": "help", "client_message_id": "id", "project_id": str(uuid4())},
    {"content": "help", "client_message_id": "id", "chapter_id": None},
    {"content": "help", "client_message_id": "id", "current_view": "Draft"},
])
async def test_home_rejects_missing_identity_and_foreign_context(client, payload):
    conv = (await client.post(BASE)).json()
    assert (await client.post(f"{BASE}/{conv['id']}/messages", json=payload)).status_code == 422
    assert (await client.get(f"{BASE}/{conv['id']}")).json()["messages"] == []
    assert (await client.post(BASE, json={"home_owner_key": "local"})).status_code == 422


async def test_home_concurrent_create_and_send_use_one_durable_exchange(async_session, monkeypatch):
    import app.services.studio_assistant_service as module

    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def create():
        async with factory() as session:
            return (await StudioAssistantService(session, home=True).get_or_create_conversation(None)).id

    ids = await asyncio.gather(*(create() for _ in range(5)))
    assert len(set(ids)) == 1
    original = module.home_assistant_reply
    calls = []

    def reply(question):
        calls.append(question)
        return original(question)

    monkeypatch.setattr(module, "home_assistant_reply", reply)

    async def send():
        async with factory() as session:
            return await StudioAssistantService(session, home=True).send_message(
                None, ids[0], HomeAssistantSendRequest(content="怎么回到书架？", client_message_id="same"),
            )

    responses = await asyncio.gather(send(), send())
    assert [len(response.messages) for response in responses] == [2, 2]
    assert len(calls) == 1
    assert await async_session.scalar(select(func.count()).select_from(StudioAssistantConversation)) == 1


async def test_failed_home_exchange_rolls_back_and_same_identity_can_retry(async_session, monkeypatch):
    import app.services.studio_assistant_service as module

    service = StudioAssistantService(async_session, home=True)
    conversation_id = (await service.get_or_create_conversation(None)).id
    original = module.home_assistant_reply

    def fail(_question):
        raise RuntimeError("Synthetic generation failure")

    monkeypatch.setattr(module, "home_assistant_reply", fail)
    payload = HomeAssistantSendRequest(content="新建作品", client_message_id="retry")
    with pytest.raises(RuntimeError, match="Synthetic"):
        await service.send_message(None, conversation_id, payload)
    assert await async_session.scalar(select(func.count()).select_from(StudioAssistantMessage)) == 0
    monkeypatch.setattr(module, "home_assistant_reply", original)
    assert len((await service.send_message(None, conversation_id, payload)).messages) == 2


async def test_project_conversation_and_retry_context_cannot_drift(client, async_session, tmp_path, monkeypatch):
    from app.agents.studio_assistant import StudioAssistantAgent

    reply = AsyncMock(return_value=("Actual mocked project response", [], []))
    monkeypatch.setattr(StudioAssistantAgent, "generate_reply", reply)
    project = Project(slug=str(uuid4()), title="Novel", workspace_root=str(tmp_path))
    async_session.add(project)
    await async_session.flush()
    first = Chapter(project_id=project.id, chapter_number=1)
    second = Chapter(project_id=project.id, chapter_number=2)
    async_session.add_all([first, second])
    await async_session.commit()
    base = f"/api/v1/projects/{project.id}/assistant/conversations"
    chapter_conv = (await client.post(base, json={"chapter_id": str(first.id)})).json()
    project_conv = (await client.post(base)).json()
    assert project_conv["id"] != chapter_conv["id"]
    assert project_conv["chapter_id"] is None
    payload = {"content": "查看正文", "chapter_id": str(first.id), "current_view": "Draft", "client_message_id": "one"}
    endpoint = f"{base}/{project_conv['id']}/messages"
    sent = await client.post(endpoint, json=payload)
    assert sent.status_code == 200, sent.text
    assert sent.json()["messages"][0]["tool_calls"][0]["context"] == {
        "chapter_id": str(first.id), "current_view": "Draft",
    }
    for change in ({"content": "changed"}, {"chapter_id": str(second.id)}, {"current_view": "Review"}):
        assert (await client.post(endpoint, json=payload | change)).status_code == 409
    assert (await client.post(endpoint, json=payload)).json() == sent.json()
    assert reply.await_count == 1
    switch = await client.post(f"{base}/{chapter_conv['id']}/messages", json=payload | {"chapter_id": str(second.id)})
    assert switch.status_code == 422
    bound_endpoint = f"{base}/{chapter_conv['id']}/messages"
    assert (await client.post(bound_endpoint, json=payload)).status_code == 200
    assert (await client.post(bound_endpoint, json=payload | {"chapter_id": str(second.id)})).status_code == 409

    # Project-level history remains replayable after its optional chapter is removed.
    await async_session.delete(first)
    await async_session.commit()
    assert (await client.post(endpoint, json=payload)).json() == sent.json()
    assert reply.await_count == 2


async def test_database_requires_exactly_one_scope_and_allows_explicit_owner_sessions(async_session, tmp_path):
    project = Project(slug=str(uuid4()), title="Novel", workspace_root=str(tmp_path))
    async_session.add(project)
    await async_session.commit()
    project_id = project.id
    for values in ({}, {"home_owner_key": "untrusted-owner"},
                   {"home_owner_key": "local", "project_id": project_id}):
        async with async_session.begin_nested():
            async_session.add(StudioAssistantConversation(**values))
            with pytest.raises(IntegrityError):
                await async_session.flush()
            await async_session.rollback()
    async_session.add(StudioAssistantConversation(home_owner_key="local"))
    await async_session.commit()
    async_session.add(StudioAssistantConversation(home_owner_key="local"))
    await async_session.commit()
    assert await async_session.scalar(select(func.count()).select_from(StudioAssistantConversation).where(
        StudioAssistantConversation.home_owner_key == "local",
    )) == 2
