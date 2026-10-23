"""Collection authoring sessions pin all work without changing legacy defaults."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.core.config import settings
from app.models import DocumentSource, DocumentType, SettingCollaboration, SettingCollaborationMessage, User
from app.services.document_service import DocumentService
from tests.integration.test_setting_collaboration_recovery import assert_pair, client as client, generated, seed

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def create(client, path, request_id=None, *, headers=None):
    request_id = request_id or str(uuid4())
    response = await client.post(path + "/conversations", json={"client_request_id": request_id}, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["client_request_id"] == request_id and result["is_default"] is False
    return result, f"{path}/conversations/{result['id']}"


async def test_create_replay_and_concurrency_keep_one_empty_session_and_a_stable_default(client, async_session, tmp_path, monkeypatch):
    _, path, _ = await seed(async_session, tmp_path)
    calls = []
    generated(monkeypatch, calls)
    assert (await client.get(path + "/conversations")).json() == {"items": [], "next_offset": None}
    for invalid in ({}, {"client_request_id": str(UUID(int=0))}, {"client_request_id": str(uuid4()), "is_default": True}):
        assert (await client.post(path + "/conversations", json=invalid)).status_code == 422
    request = {"client_request_id": str(uuid4())}
    first, second = await asyncio.gather(client.post(path + "/conversations", json=request), client.post(path + "/conversations", json=request))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["client_request_id"] == request["client_request_id"]
    assert first.json()["title"] == "新对话" and first.json()["messages"] == first.json()["attempts"] == []
    assert (await client.get(path)).status_code == 404  # Explicit creation does not replace legacy default.
    default = (await client.post(path)).json()
    assert default["is_default"] is True and default["client_request_id"] is None
    assert default["id"] != first.json()["id"]
    newer, pinned = await create(client, path)
    inspected = (await client.get(pinned)).json()
    assert inspected["id"] == newer["id"] and inspected["updated_at"] == newer["updated_at"]
    assert (await client.post(path)).json()["id"] == default["id"]
    assert (await client.get(path)).json()["id"] == default["id"]
    assert (await client.post(path + "/conversations", json=request)).json()["id"] == first.json()["id"]
    assert await async_session.scalar(select(func.count()).select_from(SettingCollaboration)) == 3
    assert (await client.get(path + "/conversations")).json() == {"items": [], "next_offset": None}
    assert calls == []


async def test_pinned_sends_history_and_same_message_identity_are_isolated_between_sessions(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    calls = []
    generated(monkeypatch, calls)
    default = (await client.post(path)).json()
    first, a = await create(client, path)
    second, b = await create(client, path)
    a_reply = await client.post(a + "/messages", json=payload)
    b_reply = await client.post(b + "/messages", json=payload)
    assert a_reply.status_code == b_reply.status_code == 200
    assert_pair(a_reply.json(), payload["client_message_id"])
    assert_pair(b_reply.json(), payload["client_message_id"])
    assert a_reply.json()["id"] == first["id"] and b_reply.json()["id"] == second["id"]
    assert calls[0][2] == calls[1][2] == []
    assert (await client.post(a + "/messages", json=payload)).json()["messages"] == a_reply.json()["messages"]
    follow_up = {**payload, "client_message_id": "only-a", "content": "Continue the first conversation"}
    assert (await client.post(a + "/messages", json=follow_up)).status_code == 200
    assert [item["role"] for item in calls[2][2]] == ["user", "assistant"]
    assert len((await client.get(a)).json()["messages"]) == 4
    assert len((await client.get(b)).json()["messages"]) == 2
    untouched = (await client.get(path)).json()
    assert untouched["id"] == default["id"] and untouched["messages"] == untouched["attempts"] == []
    assert len(calls) == 3


async def test_pending_attempt_inspection_resume_finish_and_foreign_sessions_never_fall_back(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    first, a = await create(client, path)
    _, b = await create(client, path)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    assert (await client.post(a + "/messages", json=payload)).status_code == 503
    attempt = (await client.get(a)).json()["attempts"][0]
    calls = []
    generated(monkeypatch, calls)
    assert (await client.post(b + "/messages", json=payload)).status_code == 200
    for wrong in (b, path):
        assert (await client.get(f"{wrong}/attempts/{attempt['id']}")).status_code == 404
        assert (await client.post(f"{wrong}/attempts/{attempt['id']}/resume")).status_code == 404
    _, other, _ = await seed(async_session, tmp_path / "other")
    other_created, _ = await create(client, other, first["client_request_id"])
    assert other_created["id"] != first["id"]
    foreign = f"{other}/conversations/{first['id']}"
    assert (await client.get(foreign)).status_code == 404
    assert (await client.post(foreign + "/messages", json=payload)).status_code == 404
    assert (await client.post(foreign + "/attempts/finish", json=payload)).status_code == 404
    assert (await client.get(other)).status_code == 404
    finished = await client.post(a + "/attempts/finish", json=payload)
    assert finished.status_code == 200 and finished.json()["id"] == first["id"]
    assert finished.json()["attempts"][0]["status"] == "finished"
    assert (await client.post(a + "/messages", json=payload)).json()["messages"] == []
    assert len((await client.get(b)).json()["messages"]) == 2 and len(calls) == 1


async def test_late_response_stays_pinned_when_another_session_is_created_and_used(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    first, a = await create(client, path)
    calls, started, finish = [], asyncio.Event(), asyncio.Event()
    generated(monkeypatch, calls, started=started, finish=finish)
    pending = asyncio.create_task(client.post(a + "/messages", json=payload))
    await asyncio.wait_for(started.wait(), timeout=3)
    try:
        second, b = await create(client, path)
        generated(monkeypatch, calls)
        b_reply = await client.post(b + "/messages", json={**payload, "content": "An independent discussion"})
        assert b_reply.status_code == 200 and b_reply.json()["id"] == second["id"]
    finally:
        finish.set()
    a_reply = await pending
    assert a_reply.status_code == 200 and a_reply.json()["id"] == first["id"]
    assert a_reply.json()["messages"][0]["content"] == payload["content"]
    assert b_reply.json()["messages"][0]["content"] == "An independent discussion"
    assert calls[0][2] == calls[1][2] == []


async def test_proposals_can_only_be_adopted_or_discarded_in_their_original_session(client, async_session, tmp_path, monkeypatch):
    collection, path, payload = await seed(async_session, tmp_path)
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="Tide", path="world/tide.md", content="Original tide", source=DocumentSource.USER,
    )
    _, a = await create(client, path)
    _, b = await create(client, path)
    from tests.integration.test_setting_collection_collaboration import acceptance, generated as suggested
    suggested(monkeypatch, [], target_title="Tide")
    response = await client.post(a + "/messages", json=payload)
    assert response.status_code == 200, response.text
    message = response.json()["messages"][1]
    proposal_id = message["proposal"]["id"]
    assert (await client.post(f"{b}/proposals/{proposal_id}/accept", json=acceptance(message))).status_code == 404
    assert (await client.post(f"{b}/proposals/{proposal_id}/discard")).status_code == 404
    assert (await client.get(b)).json()["messages"] == []
    assert (await client.get(f"/api/v1/documents/{document.id}/content")).json()["content"] == "Original tide"
    adopted = await client.post(f"{a}/proposals/{proposal_id}/accept", json=acceptance(message))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["conversation"]["id"] == response.json()["id"]
    assert (await client.post(f"{a}/proposals/{proposal_id}/accept", json=acceptance(message))).status_code == 200
    assert len((await client.get(f"/api/v1/documents/{document.id}/versions")).json()) == 2


async def test_request_title_is_initialized_once_and_pending_history_is_not_blank_success(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    first, a = await create(client, path)
    calls = []
    generated(monkeypatch, calls)
    assert (await client.post(a + "/messages", json={**payload, "content": "新对话"})).json()["title"] == "新对话"
    assert (await client.post(a + "/messages", json={**payload, "client_message_id": "second", "content": "Do not rename the first title"})).json()["title"] == "新对话"
    second, b = await create(client, path)
    from app.agents import setting_collaboration as agent
    from app.llm.errors import ProviderConfigurationError

    async def unconfigured(*args, **kwargs):
        raise ProviderConfigurationError()

    monkeypatch.setattr(agent, "generate_setting_collection_collaboration", unconfigured)
    content = "Pending original input " + "x" * 200
    assert (await client.post(b + "/messages", json={**payload, "content": content})).status_code == 503
    summaries = (await client.get(path + "/conversations")).json()["items"]
    summary = next(item for item in summaries if item["id"] == second["id"])
    assert summary["title"] == content[:48] and summary["preview"] == content[:160]
    assert summary["message_count"] == 0
    assert next(item for item in summaries if item["id"] == first["id"])["message_count"] == 4
    assert len(calls) == 2


async def test_first_input_title_is_not_overwritten_after_finishing_without_a_complete_pair(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    _, pinned = await create(client, path)
    ended = await client.post(pinned + "/attempts/finish", json={**payload, "content": "新对话"})
    assert ended.status_code == 200 and ended.json()["title"] == "新对话"
    assert ended.json()["messages"] == []
    calls = []
    generated(monkeypatch, calls)
    sent = await client.post(pinned + "/messages", json={**payload, "client_message_id": "new-after-finish", "content": "Do not replace the first title"})
    assert sent.status_code == 200 and sent.json()["title"] == "新对话"
    assert len(calls) == 1


async def test_history_pagination_is_stable_lightweight_and_never_loads_frozen_bodies(client, async_session, tmp_path, monkeypatch):
    _, path, _ = await seed(async_session, tmp_path)
    calls = []
    generated(monkeypatch, calls)
    ids = [(await create(client, path))[0]["id"] for _ in range(4)]
    for identity in ids:
        response = await client.post(f"{path}/conversations/{identity}/messages", json={"client_message_id": str(uuid4()), "content": "A used conversation for history pagination"})
        assert response.status_code == 200, response.text
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    for identity in ids:
        conversation = await async_session.get(SettingCollaboration, UUID(identity))
        conversation.updated_at = timestamp
    await async_session.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(async_session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        first = (await client.get(path + "/conversations?limit=2")).json()
        second = (await client.get(path + f"/conversations?limit=2&offset={first['next_offset']}")).json()
    finally:
        event.remove(async_session.bind.sync_engine, "before_cursor_execute", capture)
    assert [item["id"] for item in first["items"] + second["items"]] == sorted(ids, reverse=True)
    assert first["next_offset"] == 2 and second["next_offset"] is None
    assert set(first["items"][0]) == {"id", "title", "created_at", "updated_at", "message_count", "preview", "is_default"}
    assert all(item["message_count"] == 2 for item in first["items"] + second["items"])
    assert all("setting_collaboration_attempts.context" not in sql and "setting_collaboration_attempts.history" not in sql and "setting_collaboration_messages.proposal" not in sql for sql in statements)
    assert (await client.get(path + "/conversations?limit=0")).status_code == 422
    assert (await client.get(path + "/conversations?limit=101")).status_code == 422
    assert (await client.get(path + "/conversations?offset=-1")).status_code == 422
    assert len(calls) == 4  # Only the setup sends generate; listing never does.


async def test_history_filters_empty_rows_before_pagination_but_keeps_every_durable_request(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    default = (await client.post(path)).json()
    empty, empty_path = await create(client, path)
    other_empty, _ = await create(client, path)
    hidden = {default["id"], empty["id"], other_empty["id"]}
    prepared, prepared_path = await create(client, path)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    assert (await client.post(prepared_path + "/messages", json=payload)).status_code == 503
    assert (await client.get(prepared_path)).json()["attempts"][0]["status"] == "prepared"
    dispatched, dispatched_path = await create(client, path)
    from app.llm.errors import ProviderTimeoutError

    async def timeout(content, context, history, *, before_dispatch):
        await before_dispatch({"model_identifier": "deterministic-history-test"})
        raise ProviderTimeoutError()

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", timeout)
    assert (await client.post(dispatched_path + "/messages", json=payload)).status_code == 504
    assert (await client.get(dispatched_path)).json()["attempts"][0]["status"] == "dispatched"
    finished, finished_path = await create(client, path)
    assert (await client.post(finished_path + "/attempts/finish", json=payload)).json()["attempts"][0]["status"] == "finished"
    completed, completed_path = await create(client, path)
    calls = []
    generated(monkeypatch, calls)
    assert (await client.post(completed_path + "/messages", json=payload)).status_code == 200
    legacy, _ = await create(client, path)
    async_session.add(SettingCollaborationMessage(conversation_id=UUID(legacy["id"]), role="user", content="Existing historical input without an attempt"))
    visible = [prepared["id"], dispatched["id"], finished["id"], completed["id"], legacy["id"]]
    # Empty rows are deliberately newer than all used rows. Filtering after
    # LIMIT/OFFSET would hide real history or produce incorrect next_offset.
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    for index, identity in enumerate(visible):
        conversation = await async_session.get(SettingCollaboration, UUID(identity))
        conversation.updated_at = timestamp + timedelta(seconds=index)
    for identity in hidden:
        conversation = await async_session.get(SettingCollaboration, UUID(identity))
        conversation.updated_at = timestamp + timedelta(days=1)
    await async_session.commit()
    pages = [(await client.get(path + f"/conversations?limit=2&offset={offset}")).json() for offset in (0, 2, 4)]
    summaries = [item for page in pages for item in page["items"]]
    assert [item["id"] for item in summaries] == list(reversed(visible))
    assert [page["next_offset"] for page in pages] == [2, 4, None]
    assert all(item["id"] not in hidden and item["preview"] for item in summaries)
    assert {item["id"]: item["message_count"] for item in summaries} == {
        prepared["id"]: 0, dispatched["id"]: 0, finished["id"]: 0, completed["id"]: 2, legacy["id"]: 1,
    }
    assert all(set(item) == {"id", "title", "created_at", "updated_at", "message_count", "preview", "is_default"} for item in summaries)
    assert await async_session.scalar(select(func.count()).select_from(SettingCollaboration)) == 8
    assert (await client.get(empty_path)).json()["id"] == empty["id"]
    assert (await client.get(path)).json()["id"] == default["id"]
    replay = await client.post(path + "/conversations", json={"client_request_id": empty["client_request_id"]})
    assert replay.json()["id"] == empty["id"]
    assert len(calls) == 1


async def test_history_owner_archive_creation_replay_and_legacy_document_boundaries(client, async_session, tmp_path, monkeypatch):
    calls = []
    generated(monkeypatch, calls)
    collection, path, _ = await seed(async_session, tmp_path)
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    collection.owner_id = owner.id
    await async_session.commit()
    headers = {"x-actor-user-id": str(stranger.id)}
    created, pinned = await create(client, path)
    assert (await client.get(path + "/conversations", headers=headers)).status_code == 403
    assert (await client.get(pinned, headers=headers)).status_code == 403
    assert (await client.post(path + "/conversations", json={"client_request_id": str(uuid4())}, headers=headers)).status_code == 403
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="Old document", path="world/old.md", content="Old saved entry", source=DocumentSource.USER,
    )
    legacy = f"/api/v1/setting-collections/{collection.id}/documents/{document.id}/collaboration"
    old = (await client.post(legacy)).json()
    assert (await client.get(f"{path}/conversations/{old['id']}")).status_code == 404
    assert (await client.get(legacy)).json()["id"] == old["id"]
    assert all(item["id"] != old["id"] for item in (await client.get(path + "/conversations")).json()["items"])
    collection.status = "archived"
    await async_session.commit()
    assert (await client.get(pinned)).status_code == 200
    assert (await client.get(path + "/conversations")).status_code == 200
    assert (await client.post(path + "/conversations", json={"client_request_id": str(uuid4())})).status_code == 409
    replay = await client.post(path + "/conversations", json={"client_request_id": created["client_request_id"]})
    assert replay.status_code == 200 and replay.json()["id"] == created["id"]
    assert calls == []
