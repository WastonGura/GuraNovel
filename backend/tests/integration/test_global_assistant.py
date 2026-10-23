"""Owner-bound Gura sessions continue across real page contexts without merging old history."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_db_session
from app.core.config import settings
from app.llm.errors import ProviderUnavailableError
from app.main import create_app
from app.models import Chapter, DocumentSource, DocumentType, Project, SettingCollection, StudioAssistantConversation, StudioAssistantMessage, User
from app.services.document_service import DocumentService
from app.services.studio_assistant_service import StudioAssistantService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
BASE = "/api/v1/assistant/conversations"
LEGACY = "/api/v1/assistant/legacy-conversations"


@pytest.fixture
async def client(async_session, monkeypatch):
    for field in ("openai_compatible_base_url", "openai_compatible_api_key", "openai_compatible_model"):
        monkeypatch.setattr(settings, field, None)
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def seed(session, tmp_path, *, owner_id=None):
    for workspace_root in (tmp_path / "novel", tmp_path / "world"):
        workspace_root.mkdir(parents=True, exist_ok=True)
    project = Project(slug=str(uuid4()), title="Novel A", workspace_root=str(tmp_path / "novel"), owner_id=owner_id)
    collection = SettingCollection(slug=str(uuid4()), title="World B", workspace_root=str(tmp_path / "world"), owner_id=owner_id)
    session.add_all([project, collection])
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1, title="Chapter one")
    session.add(chapter)
    await session.commit()
    return project, chapter, collection


def payload(identity, context, text=None):
    return {"content": text or f"Question {identity}", "client_message_id": identity, "context": context}


async def test_one_conversation_continues_home_project_chapter_collection_and_home(client, async_session, tmp_path, monkeypatch):
    project, chapter, collection = await seed(async_session, tmp_path)
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="Tide", path="tide.md", content="Saved tide fact.", source=DocumentSource.USER,
    )
    home_calls, project_calls, collection_calls = [], [], []

    async def home(question, context, history):
        home_calls.append((context, history))
        return "Home follow-up"

    async def project_reply(self, **kwargs):
        project_calls.append((self.project_id, kwargs))
        return "Project reply", [], []

    async def collection_reply(question, context, history):
        collection_calls.append((context, history))
        return "Collection reply"

    monkeypatch.setattr("app.services.global_assistant_service.generate_home_reply", home)
    monkeypatch.setattr("app.agents.studio_assistant.StudioAssistantAgent.generate_reply", project_reply)
    monkeypatch.setattr("app.agents.setting_assistant.generate_setting_reply", collection_reply)
    conversation = (await client.post(BASE)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    contexts = [
        {"kind": "home", "current_view": "home"},
        {"kind": "project", "project_id": str(project.id), "current_view": "ProjectWorkspace"},
        {"kind": "project", "project_id": str(project.id), "chapter_id": str(chapter.id), "current_view": "Draft"},
        {"kind": "collection", "collection_id": str(collection.id), "document_id": str(document.id), "document_version_id": str(document.current_version_id)},
        {"kind": "home"},
    ]
    for index, context in enumerate(contexts):
        sent = await client.post(endpoint, json=payload(str(index), context))
        assert sent.status_code == 200, sent.text
        assert sent.json()["id"] == conversation["id"]
        assert sent.json()["messages"][-2]["tool_calls"] == [{"client_message_id": str(index), "context": context, "exchange_complete": True}]
    loaded = (await client.get(f"{BASE}/{conversation['id']}")).json()
    assert len(loaded["messages"]) == 10
    assert loaded["title"] == "Question 0"
    assert loaded["project_id"] is loaded["setting_collection_id"] is loaded["chapter_id"] is None
    assert [len(call[1]["history"]) for call in project_calls] == [2, 4]
    assert project_calls[0][0] == project.id
    assert project_calls[1][1]["chapter_id"] == chapter.id
    assert collection_calls[0][0]["body"] == "Saved tide fact."
    assert len(collection_calls[0][1]) == 6
    assert len(home_calls[-1][1]) == 8
    assert home_calls[-1][0] == {"kind": "home"}
    assert "历史页面上下文，仅作资料" in home_calls[-1][1][0]["content"]
    assert any(str(collection.id) in message["content"] for message in home_calls[-1][1])
    summary = (await client.get(BASE)).json()[0]
    assert set(summary) == {"id", "title", "created_at", "updated_at", "message_count", "preview"}
    assert summary["message_count"] == 10 and summary["preview"] == "Home follow-up"
    assert (await client.post(BASE)).json()["id"] == conversation["id"]


async def test_explicit_new_sessions_and_lost_create_ack_are_idempotent(client, async_session):
    first = (await client.post(BASE)).json()
    identity = str(uuid4())
    body = {"new_conversation": True, "client_conversation_id": identity}
    second = await client.post(BASE, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == identity != first["id"]
    assert second.json()["title"] == "新对话"
    assert second.json()["messages"] == []
    results = await asyncio.gather(*(client.post(BASE, json=body) for _ in range(3)))
    assert all(result.json()["id"] == identity for result in results)
    for _ in range(3):
        assert (await client.post(BASE)).json()["id"] == identity
    assert len((await client.get(BASE)).json()) == 2
    assert (await client.post(BASE, json={"client_conversation_id": identity})).status_code == 422
    foreign = await client.post(BASE, json=body, headers={"x-actor-user-id": str(uuid4())})
    assert foreign.status_code == 404
    assert await async_session.scalar(select(func.count()).select_from(StudioAssistantConversation)) == 2


async def test_owner_isolation_and_exact_context_identity_before_mutable_lookup(client):
    owner = {"x-actor-user-id": str(uuid4())}
    stranger = {"x-actor-user-id": str(uuid4())}
    conversation = (await client.post(BASE, headers=owner)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    original = payload("one", {"kind": "home"})
    sent = await client.post(endpoint, json=original, headers=owner)
    assert sent.status_code == 200, sent.text
    assert (await client.post(endpoint, json=original, headers=owner)).json() == sent.json()
    for changed in (payload("one", {"kind": "home", "current_view": None}),
                    payload("one", {"kind": "project", "project_id": str(uuid4())}),
                    payload("one", {"kind": "home"}, "Different text")):
        assert (await client.post(endpoint, json=changed, headers=owner)).status_code == 409
    assert (await client.get(BASE, headers=stranger)).json() == []
    assert (await client.get(f"{BASE}/{conversation['id']}", headers=stranger)).status_code == 404
    assert (await client.post(endpoint, json=original, headers=stranger)).status_code == 404
    assert (await client.get(f"{BASE}/{conversation['id']}")).status_code == 404


@pytest.mark.parametrize("context", [
    {}, {"kind": "project"}, {"kind": "collection"}, {"kind": "unknown"},
    {"kind": "home", "project_id": str(uuid4())},
    {"kind": "project", "project_id": str(uuid4()), "collection_id": str(uuid4())},
    {"kind": "collection", "collection_id": str(uuid4()), "chapter_id": str(uuid4())},
])
async def test_global_context_schema_rejects_ambiguous_or_missing_scope(client, context):
    conversation = (await client.post(BASE)).json()
    response = await client.post(f"{BASE}/{conversation['id']}/messages", json=payload("one", context))
    assert response.status_code == 422
    assert (await client.get(f"{BASE}/{conversation['id']}")).json()["messages"] == []


async def test_current_context_permissions_and_chapter_document_membership(client, async_session, tmp_path, monkeypatch):
    owner = User(username=str(uuid4()))
    stranger = User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    project, chapter, collection = await seed(async_session, tmp_path, owner_id=owner.id)
    other_project, other_chapter, other_collection = await seed(async_session, tmp_path / "other")
    headers = {"x-actor-user-id": str(stranger.id)}
    conversation = (await client.post(BASE, headers=headers)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    for context in ({"kind": "project", "project_id": str(project.id)},
                    {"kind": "collection", "collection_id": str(collection.id)}):
        assert (await client.post(endpoint, json=payload(str(uuid4()), context), headers=headers)).status_code == 403
    mismatch = {"kind": "project", "project_id": str(other_project.id), "chapter_id": str(chapter.id)}
    assert (await client.post(endpoint, json=payload("wrong-chapter", mismatch), headers=headers)).status_code == 404
    bad_document = {"kind": "collection", "collection_id": str(other_collection.id), "document_id": str(uuid4())}
    assert (await client.post(endpoint, json=payload("wrong-document", bad_document), headers=headers)).status_code == 404
    orphan_version = {"kind": "collection", "collection_id": str(other_collection.id), "document_version_id": str(uuid4())}
    assert (await client.post(endpoint, json=payload("orphan-version", orphan_version), headers=headers)).status_code == 422
    assert (await client.get(f"{BASE}/{conversation['id']}", headers=headers)).json()["messages"] == []


async def test_duplicate_global_delivery_and_failed_reply_roll_back(client, monkeypatch):
    fake = AsyncMock(return_value="Saved reply")
    monkeypatch.setattr("app.services.global_assistant_service.generate_home_reply", fake)
    conversation = (await client.post(BASE)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    original = payload("same", {"kind": "home"})
    results = await asyncio.gather(*(client.post(endpoint, json=original) for _ in range(3)))
    assert all(result.status_code == 200 and len(result.json()["messages"]) == 2 for result in results)
    assert fake.await_count == 1
    fresh = (await client.post(BASE, json={"new_conversation": True})).json()
    fresh_endpoint = f"{BASE}/{fresh['id']}/messages"
    fake.side_effect = ProviderUnavailableError()
    assert (await client.post(fresh_endpoint, json=original)).status_code == 503
    failed = (await client.get(f"{BASE}/{fresh['id']}")).json()
    assert failed["title"] == "新对话" and failed["messages"] == []
    fake.side_effect = None
    assert len((await client.post(fresh_endpoint, json=original)).json()["messages"]) == 2


async def test_legacy_histories_are_explicit_read_only_and_never_merged(client, async_session, tmp_path):
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    project, chapter, collection = await seed(async_session, tmp_path, owner_id=owner.id)
    project_conversation = await StudioAssistantService(async_session).get_or_create_conversation(project.id, chapter.id)
    collection_conversation = await StudioAssistantService(async_session, setting_collection_id=collection.id).get_or_create_conversation(None)
    for conversation in (project_conversation, collection_conversation):
        async_session.add(StudioAssistantMessage(conversation_id=conversation.id, role="user", content="Old history"))
    await async_session.commit()
    global_conversation = (await client.post(BASE)).json()
    assert [item["id"] for item in (await client.get(BASE)).json()] == [global_conversation["id"]]
    for query, conversation in ((f"project_id={project.id}", project_conversation),
                                (f"collection_id={collection.id}", collection_conversation)):
        listing = await client.get(f"{LEGACY}?{query}")
        assert listing.status_code == 200, listing.text
        assert listing.json()[0]["id"] == str(conversation.id)
        assert listing.json()[0]["message_count"] == 1
        loaded = await client.get(f"{LEGACY}/{conversation.id}?{query}")
        assert loaded.status_code == 200 and loaded.json()["messages"][0]["content"] == "Old history"
        assert (await client.get(f"{LEGACY}?{query}", headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
        assert (await client.get(f"{LEGACY}/{conversation.id}?{query}", headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
        assert (await client.post(f"{BASE}/{conversation.id}/messages", json=payload("never-rebind", {"kind": "home"}))).status_code == 404
    assert (await client.get(LEGACY)).status_code == 422
    assert (await client.get(f"{LEGACY}?project_id={project.id}&collection_id={collection.id}")).status_code == 422
    assert (await client.get(f"{LEGACY}/{global_conversation['id']}?project_id={project.id}")).status_code == 404


async def test_real_chapter_creation_and_duplicate_delivery_commit_with_complete_exchange(client, async_session, tmp_path, monkeypatch):
    from app.services import studio_assistant_tools as tools
    project, _, _ = await seed(async_session, tmp_path)
    original_tool = tools.tool_create_chapter
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause_after_create(session, project_id, title=None):
        result = await original_tool(session, project_id, title)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(tools, "tool_create_chapter", pause_after_create)
    conversation = (await client.post(BASE)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    request = payload("real-create", {"kind": "project", "project_id": str(project.id)}, "帮我新建一章名为原子章节")
    first = asyncio.create_task(client.post(endpoint, json=request))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        # PostgreSQL sees neither the new chapter nor a half-committed user message.
        assert await async_session.scalar(select(func.count()).select_from(Chapter).where(
            Chapter.project_id == project.id, Chapter.title == "原子章节",
        )) == 0
        assert await async_session.scalar(select(func.count()).select_from(StudioAssistantMessage)) == 0
        duplicate = asyncio.create_task(client.post(endpoint, json=request))
    finally:
        release.set()
    results = await asyncio.gather(first, duplicate)
    assert all(result.status_code == 200 and len(result.json()["messages"]) == 2 for result in results)
    assert results[0].json()["messages"][0]["tool_calls"][0]["exchange_complete"] is True
    assert await async_session.scalar(select(func.count()).select_from(Chapter).where(
        Chapter.project_id == project.id, Chapter.title == "原子章节",
    )) == 1


async def test_failed_real_chapter_tool_rolls_back_chapter_and_pair_then_retry_is_safe(client, async_session, tmp_path, monkeypatch):
    from app.services import studio_assistant_tools as tools
    project, _, _ = await seed(async_session, tmp_path)
    original_tool = tools.tool_create_chapter

    async def fail_after_create(session, project_id, title=None):
        await original_tool(session, project_id, title)
        raise ProviderUnavailableError()

    monkeypatch.setattr(tools, "tool_create_chapter", fail_after_create)
    conversation = (await client.post(BASE)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    request = payload("retry-create", {"kind": "project", "project_id": str(project.id)}, "帮我新建一章名为失败重试章节")
    assert (await client.post(endpoint, json=request)).status_code == 503
    assert (await client.get(f"{BASE}/{conversation['id']}")).json()["messages"] == []
    assert await async_session.scalar(select(func.count()).select_from(Chapter).where(Chapter.title == "失败重试章节")) == 0
    monkeypatch.setattr(tools, "tool_create_chapter", original_tool)
    assert (await client.post(endpoint, json=request)).status_code == 200
    assert await async_session.scalar(select(func.count()).select_from(Chapter).where(Chapter.title == "失败重试章节")) == 1


async def test_independent_workflow_commit_is_never_false_ack_or_duplicate_operation(client, async_session, tmp_path, monkeypatch):
    from app.services.studio_assistant_service import _CONVERSATION_LOCKS
    project, _, _ = await seed(async_session, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def independently_committing_workflow(self, **kwargs):
        calls.append(1)
        await self.session.commit()
        entered.set()
        await release.wait()
        raise ProviderUnavailableError()

    monkeypatch.setattr("app.agents.studio_assistant.StudioAssistantAgent.generate_reply", independently_committing_workflow)
    conversation = (await client.post(BASE)).json()
    endpoint = f"{BASE}/{conversation['id']}/messages"
    request = payload("workflow", {"kind": "project", "project_id": str(project.id)}, "提交审阅")
    first = asyncio.create_task(client.post(endpoint, json=request))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        partial = (await client.get(f"{BASE}/{conversation['id']}")).json()
        assert len(partial["messages"]) == 1
        assert partial["messages"][0]["tool_calls"][0]["exchange_complete"] is False
        # A different process has a different in-memory lock; exercise the durable DB guard.
        from uuid import UUID
        _CONVERSATION_LOCKS[UUID(conversation["id"])] = asyncio.Lock()
        duplicate = await client.post(endpoint, json=request)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "assistant_exchange_unconfirmed"
    finally:
        release.set()
    assert (await first).status_code == 503
    for candidate in (request, payload("another-id", {"kind": "home"})):
        blocked = await client.post(endpoint, json=candidate)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "assistant_exchange_unconfirmed"
    assert len(calls) == 1
