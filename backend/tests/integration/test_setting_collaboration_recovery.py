"""A paid dispatch identity survives uncertainty without silently calling again."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.api.schemas_setting_collaboration import SettingCollaborationSendRequest, SettingCollectionCollaborationSendRequest
from app.core.config import settings
from app.llm.errors import ProviderTimeoutError
from app.main import create_app
from app.models import Document, DocumentSource, DocumentType, SettingCollection, SettingCollaboration, SettingCollaborationAttempt, SettingCollaborationMessage, User
from app.services.document_service import DocumentService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session):
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as value:
        yield value


async def seed(session, tmp_path, scope="collection"):
    root = tmp_path / "setting-workspace"
    # The real POSIX MarkdownStore requires a workspace created by its owner.
    root.mkdir(parents=True, exist_ok=True)
    collection = SettingCollection(title="Recovery evidence", slug=str(uuid4()), workspace_root=str(root))
    session.add(collection)
    await session.commit()
    payload = {"client_message_id": "one-paid-dispatch", "content": "Discuss the saved rules."}
    if scope == "document":
        document = await DocumentService(session).create_document(
            setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
            title="Tide", path="world/tide.md", content="The tide turns at midnight.", source=DocumentSource.USER,
        )
        path = f"/api/v1/setting-collections/{collection.id}/documents/{document.id}/collaboration"
        payload.update(base_version_id=str(document.current_version_id), draft_content="The tide turns at midnight.")
    else:
        path = f"/api/v1/setting-collections/{collection.id}/collaboration"
    return collection, path, payload


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_dispatched_timeout_is_durable_and_same_identity_never_calls_again(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    calls = []

    async def timeout(content, context, history, *, before_dispatch=None):
        if before_dispatch is not None:
            await before_dispatch({"model_identifier": "deterministic-test"})
        calls.append((content, context, history))
        raise ProviderTimeoutError()

    function = "generate_setting_collection_collaboration" if scope == "collection" else "generate_setting_collaboration"
    monkeypatch.setattr(f"app.agents.setting_collaboration.{function}", timeout)
    assert (await client.post(path)).status_code == 200
    assert (await client.post(path + "/messages", json=payload)).status_code == 504
    replay = await client.post(path + "/messages", json=payload)
    assert len(calls) == 1
    assert replay.status_code == 200, replay.text
    recovered = (await client.get(path)).json()
    assert recovered["messages"] == []
    assert recovered["attempts"][0]["status"] == "dispatched"
    assert recovered["attempts"][0]["client_message_id"] == payload["client_message_id"]
    assert recovered["attempts"][0]["error_code"] == "provider_timeout"


def generated(monkeypatch, calls, scope="collection", *, started=None, finish=None):
    from app.agents.setting_collaboration import SettingCollaborationReply, SettingCollectionCollaborationReply

    async def generate(content, context, history, *, before_dispatch):
        await before_dispatch({"model_identifier": "deterministic-test"})
        calls.append((content, context, history))
        if started is not None:
            started.set()
        if finish is not None:
            await finish.wait()
        schema = SettingCollectionCollaborationReply if scope == "collection" else SettingCollaborationReply
        return schema(reply="A real server-side deterministic test response; no document write.", suggestion=None), {"model_identifier": "deterministic-test", "input_tokens": 9, "output_tokens": 4}

    function = "generate_setting_collection_collaboration" if scope == "collection" else "generate_setting_collaboration"
    monkeypatch.setattr(f"app.agents.setting_collaboration.{function}", generate)


def assert_pair(conversation, client_id):
    attempt = next(item for item in conversation["attempts"] if item["client_message_id"] == client_id)
    assert attempt["status"] == "completed"
    user = next(item for item in conversation["messages"] if item["id"] == attempt["user_message_id"])
    assistant = next(item for item in conversation["messages"] if item["id"] == attempt["assistant_message_id"])
    assert user["role"] == "user" and assistant["role"] == "assistant"
    assert user["attempt_id"] == assistant["attempt_id"] == attempt["id"]
    assert user["client_message_id"] == client_id
    assert user["content"] == attempt["request"]["content"]
    assert attempt["has_result"] is True
    return attempt


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_message_commit_failure_replays_checkpoint_without_another_dispatch(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    calls, failures = [], []
    generated(monkeypatch, calls, scope)
    original = AsyncSession.commit

    async def fail_pair_commit(session):
        if not failures and any(isinstance(item, SettingCollaborationMessage) for item in session.new):
            failures.append(True)
            raise RuntimeError("Synthetic database commit failure after result checkpoint")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_pair_commit)
    await client.post(path)
    failed = await client.post(path + "/messages", json=payload)
    assert failed.status_code == 500, failed.text
    recovered = (await client.get(path)).json()
    assert recovered["messages"] == []
    attempt = recovered["attempts"][0]
    assert attempt["status"] == "result_saved" and attempt["has_result"]
    assert attempt["provenance"]["input_tokens"] == 9
    assert (await client.get(f"{path}/attempts/{attempt['id']}")).json() == attempt
    assert len(calls) == 1  # GET never materializes or dispatches anything.
    resumed = await client.post(f"{path}/attempts/{attempt['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    completed = assert_pair(resumed.json(), payload["client_message_id"])
    assert (await client.post(path + "/messages", json=payload)).json()["messages"] == resumed.json()["messages"]
    assert len(calls) == 1
    assert (await client.get(f"{path}/attempts/{completed['id']}")).json()["status"] == "completed"


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_config_failure_is_confirmed_not_dispatched_and_explicit_resume_uses_frozen_input(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    await client.post(path)
    assert (await client.post(path + "/messages", json=payload)).status_code == 503
    attempt = (await client.get(path)).json()["attempts"][0]
    assert attempt["status"] == "prepared" and not attempt["has_result"]
    assert attempt["error_code"] == "provider_configuration_error"
    assert attempt["request"] == {**payload, **({"selection": None} if scope == "document" else {})}
    calls = []
    generated(monkeypatch, calls, scope)
    # A different local draft cannot silently replace this request's frozen text.
    wrong = await client.post(path + "/messages", json={**payload, "content": "Another question"})
    assert wrong.status_code == 409 and calls == []
    result = await client.post(f"{path}/attempts/{attempt['id']}/resume")
    assert result.status_code == 200, result.text
    assert_pair(result.json(), payload["client_message_id"])
    assert calls[0][0] == payload["content"]
    assert calls[0][1]["collection_id"] == path.split("/")[4]


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_finish_before_first_send_creates_a_tombstone_and_a_new_identity_is_required(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    calls = []
    generated(monkeypatch, calls, scope)
    # There is no conversation yet, and the original send is delayed in transit.
    finished = await client.post(path + "/attempts/finish", json=payload)
    assert finished.status_code == 200, finished.text
    attempt = finished.json()["attempts"][0]
    assert attempt["status"] == "finished" and finished.json()["messages"] == []
    assert (await client.post(path + "/messages", json=payload)).status_code == 200
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume")).json()["messages"] == []
    assert calls == []
    assert (await client.post(path + "/attempts/finish", json={**payload, "content": "Changed"})).status_code == 409
    new_payload = {**payload, "client_message_id": "explicit-new-identity"}
    result = await client.post(path + "/messages", json=new_payload)
    assert result.status_code == 200, result.text
    assert_pair(result.json(), new_payload["client_message_id"])
    assert len(calls) == 1


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_concurrent_same_identity_dispatches_once_and_finish_fences_the_late_reply(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    calls, started, finish = [], asyncio.Event(), asyncio.Event()
    generated(monkeypatch, calls, scope, started=started, finish=finish)
    await client.post(path)
    pending = asyncio.create_task(client.post(path + "/messages", json=payload))
    await asyncio.wait_for(started.wait(), timeout=3)
    try:
        replay = await client.post(path + "/messages", json=payload)
        assert replay.status_code == 200 and replay.json()["messages"] == []
        active = replay.json()["attempts"][0]
        assert active["status"] == "dispatched"
        assert (await client.post(f"{path}/attempts/{active['id']}/resume")).json()["messages"] == []
        blocked = await client.post(path + "/messages", json={**payload, "client_message_id": "not-claimed"})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "setting_agent_attempt_pending"
        assert await async_session.scalar(select(SettingCollaborationAttempt).where(SettingCollaborationAttempt.client_message_id == "not-claimed")) is None
        finished = await client.post(path + "/attempts/finish", json=payload)
        assert finished.json()["attempts"][0]["status"] == "finished"
    finally:
        finish.set()
    late = await pending
    assert late.status_code == 200, late.text
    assert late.json()["messages"] == []
    assert late.json()["attempts"][0]["status"] == "finished"
    assert late.json()["attempts"][0]["has_result"] is True
    assert len(calls) == 1


async def test_owner_cross_scope_and_archival_guards_apply_to_recovery_without_provider_calls(client, async_session, tmp_path, monkeypatch):
    collection, path, payload = await seed(async_session, tmp_path)
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    collection.owner_id = owner.id
    await async_session.commit()
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    await client.post(path)
    assert (await client.post(path + "/messages", json=payload)).status_code == 503
    attempt = (await client.get(path)).json()["attempts"][0]
    calls = []
    generated(monkeypatch, calls)
    headers = {"x-actor-user-id": str(stranger.id)}
    assert (await client.get(f"{path}/attempts/{attempt['id']}", headers=headers)).status_code == 403
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume", headers=headers)).status_code == 403
    assert (await client.post(path + "/attempts/finish", json=payload, headers=headers)).status_code == 403
    _, other, _ = await seed(async_session, tmp_path / "other")
    await client.post(other)
    assert (await client.get(f"{other}/attempts/{attempt['id']}")).status_code == 404
    assert (await client.post(f"{other}/attempts/{attempt['id']}/resume")).status_code == 404
    collection.status = "archived"
    await async_session.commit()
    assert (await client.get(f"{path}/attempts/{attempt['id']}")).status_code == 200
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume")).status_code == 409
    assert calls == []
    # Finishing audit state is allowed without modifying archived prose.
    assert (await client.post(path + "/attempts/finish", json=payload)).json()["attempts"][0]["status"] == "finished"


async def test_completed_attempt_with_a_missing_assistant_fails_closed(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    calls = []
    generated(monkeypatch, calls)
    await client.post(path)
    completed = assert_pair((await client.post(path + "/messages", json=payload)).json(), payload["client_message_id"])
    assistant = await async_session.get(SettingCollaborationMessage, UUID(completed["assistant_message_id"]))
    await async_session.delete(assistant)
    await async_session.commit()
    replay = await client.post(path + "/messages", json=payload)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "setting_agent_attempt_unconfirmed"
    assert len(calls) == 1


async def test_result_checkpoint_commit_failure_is_unknown_and_never_regenerates(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    calls, failures = [], []
    generated(monkeypatch, calls)
    original = AsyncSession.commit

    async def fail_checkpoint(session):
        if not failures and any(isinstance(item, SettingCollaborationAttempt) and item.status == "result_saved" for item in session.dirty):
            failures.append(True)
            raise RuntimeError("Synthetic lost result checkpoint")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_checkpoint)
    await client.post(path)
    assert (await client.post(path + "/messages", json=payload)).status_code == 500
    recovered = (await client.get(path)).json()
    assert recovered["messages"] == []
    attempt = recovered["attempts"][0]
    assert attempt["status"] == "dispatched" and not attempt["has_result"]
    assert (await client.post(path + "/messages", json=payload)).status_code == 200
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume")).status_code == 200
    assert len(calls) == 1


async def test_process_cancellation_after_dispatch_does_not_release_the_identity(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    calls = []

    async def cancelled(content, context, history, *, before_dispatch):
        await before_dispatch({"model_identifier": "deterministic-test"})
        calls.append(content)
        raise asyncio.CancelledError()

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", cancelled)
    await client.post(path)
    with pytest.raises(asyncio.CancelledError):
        await client.post(path + "/messages", json=payload)
    inspected = (await client.get(path)).json()["attempts"][0]
    assert inspected["status"] == "dispatched" and inspected["error_code"] is None
    assert (await client.post(path + "/messages", json=payload)).json()["messages"] == []
    assert len(calls) == 1


async def test_competing_prepared_resumes_claim_dispatch_only_once(client, async_session, tmp_path, monkeypatch):
    _, path, payload = await seed(async_session, tmp_path)
    from app.agents.setting_collaboration import SettingCollectionCollaborationReply
    entered, release = asyncio.Event(), asyncio.Event()
    preflights, calls = [], []

    async def generate(content, context, history, *, before_dispatch):
        preflights.append(content)
        if len(preflights) == 2:
            entered.set()
        await release.wait()
        await before_dispatch({"model_identifier": "deterministic-test"})
        calls.append(content)
        return SettingCollectionCollaborationReply(reply="One durable dispatch", suggestion=None), {}

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", generate)
    await client.post(path)
    first = asyncio.create_task(client.post(path + "/messages", json=payload))
    second = asyncio.create_task(client.post(path + "/messages", json=payload))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
    finally:
        release.set()
    responses = await asyncio.gather(first, second)
    assert all(response.status_code == 200 for response in responses)
    assert len(preflights) == 2 and len(calls) == 1
    assert_pair((await client.get(path)).json(), payload["client_message_id"])


async def test_prepared_context_and_history_remain_frozen_after_the_author_edits(client, async_session, tmp_path, monkeypatch):
    from app.agents import setting_collaboration as agent
    original_generate = agent.generate_setting_collection_collaboration
    collection, path, payload = await seed(async_session, tmp_path)
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="Original rule", path="world/rule.md", content="Original saved rule", source=DocumentSource.USER,
    )
    calls = []
    generated(monkeypatch, calls)
    await client.post(path)
    assert (await client.post(path + "/messages", json=payload)).status_code == 200
    monkeypatch.setattr(agent, "generate_setting_collection_collaboration", original_generate)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    next_payload = {**payload, "client_message_id": "frozen-follow-up", "content": "Continue our previous discussion"}
    assert (await client.post(path + "/messages", json=next_payload)).status_code == 503
    attempt = next(item for item in (await client.get(path)).json()["attempts"] if item["client_message_id"] == "frozen-follow-up")
    assert attempt["status"] == "prepared"
    old_version = str(document.current_version_id)
    saved = await client.put(f"/api/v1/documents/{document.id}/content", json={"content": "Author's newer rule", "expected_current_version_id": old_version})
    assert saved.status_code == 200, saved.text
    generated(monkeypatch, calls)
    result = await client.post(f"{path}/attempts/{attempt['id']}/resume")
    assert result.status_code == 200, result.text
    frozen = calls[-1]
    assert frozen[1]["documents"][0]["body"] == "Original saved rule"
    assert frozen[1]["documents"][0]["version_id"] == old_version
    assert [item["role"] for item in frozen[2]] == ["user", "assistant"]


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_legacy_pair_replay_is_preserved_but_an_orphan_user_never_dispatches(client, async_session, tmp_path, monkeypatch, scope):
    _, path, payload = await seed(async_session, tmp_path, scope)
    conversation = (await client.post(path)).json()
    schema = SettingCollectionCollaborationSendRequest if scope == "collection" else SettingCollaborationSendRequest
    fingerprint = sha256(schema(**payload).model_dump_json().encode()).hexdigest()
    now = datetime.now(UTC)
    user = SettingCollaborationMessage(
        conversation_id=UUID(conversation["id"]), role="user", content=payload["content"],
        client_message_id=payload["client_message_id"], request_fingerprint=fingerprint, created_at=now,
    )
    async_session.add(user)
    await async_session.commit()
    calls = []
    generated(monkeypatch, calls, scope)
    orphan = await client.post(path + "/messages", json=payload)
    assert orphan.status_code == 409
    assert orphan.json()["error"]["code"] == "setting_agent_attempt_unconfirmed"
    async_session.add(SettingCollaborationMessage(
        conversation_id=UUID(conversation["id"]), role="assistant", content="Old complete reply", created_at=now + timedelta(microseconds=1),
    ))
    await async_session.commit()
    replay = await client.post(path + "/messages", json=payload)
    assert replay.status_code == 200, replay.text
    assert [item["role"] for item in replay.json()["messages"]] == ["user", "assistant"]
    assert all(item["attempt_id"] is None for item in replay.json()["messages"])
    assert replay.json()["attempts"] == [] and calls == []


@pytest.mark.parametrize("scope", ["collection", "document"])
@pytest.mark.parametrize("has_assistant", [False, True])
async def test_explicit_finish_fences_an_exact_legacy_journal_without_rewriting_history(client, async_session, tmp_path, monkeypatch, scope, has_assistant):
    _, path, payload = await seed(async_session, tmp_path, scope)
    conversation = (await client.post(path)).json()
    schema = SettingCollectionCollaborationSendRequest if scope == "collection" else SettingCollaborationSendRequest
    async_session.add(SettingCollaborationMessage(
        conversation_id=UUID(conversation["id"]), role="user", content=payload["content"],
        client_message_id=payload["client_message_id"], request_fingerprint=sha256(schema(**payload).model_dump_json().encode()).hexdigest(),
    ))
    if has_assistant:
        async_session.add(SettingCollaborationMessage(conversation_id=UUID(conversation["id"]), role="assistant", content="Old reply preserved"))
    await async_session.commit()
    original = (await client.get(path)).json()["messages"]
    calls = []
    generated(monkeypatch, calls, scope)
    assert (await client.post(path + "/attempts/finish", json={**payload, "content": "Changed input"})).status_code == 409
    ended = await client.post(path + "/attempts/finish", json=payload)
    assert ended.status_code == 200, ended.text
    assert ended.json()["messages"] == original
    assert ended.json()["attempts"][0]["status"] == "finished"
    assert ended.json()["attempts"][0]["user_message_id"] is None
    assert (await client.post(path + "/messages", json=payload)).json()["messages"] == original
    assert calls == []
    next_request = {**payload, "client_message_id": str(uuid4())}
    completed = await client.post(path + "/messages", json=next_request)
    assert_pair(completed.json(), next_request["client_message_id"])
    assert len(calls) == 1


async def test_archived_during_dispatch_keeps_checkpoint_without_materializing_messages(client, async_session, tmp_path, monkeypatch):
    collection, path, payload = await seed(async_session, tmp_path)
    calls, started, finish = [], asyncio.Event(), asyncio.Event()
    generated(monkeypatch, calls, started=started, finish=finish)
    await client.post(path)
    pending = asyncio.create_task(client.post(path + "/messages", json=payload))
    await asyncio.wait_for(started.wait(), timeout=3)
    try:
        collection.status = "archived"
        await async_session.commit()
    finally:
        finish.set()
    response = await pending
    assert response.status_code == 409, response.text
    recovered = (await client.get(path)).json()
    assert recovered["messages"] == []
    attempt = recovered["attempts"][0]
    assert attempt["status"] == "result_saved" and attempt["has_result"]
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume")).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["finish", "resume"])
async def test_attempt_summaries_are_bounded_but_include_the_current_explicit_operation(client, async_session, tmp_path, monkeypatch, operation):
    _, path, payload = await seed(async_session, tmp_path)
    monkeypatch.setattr(settings, "openai_compatible_api_key", None)
    conversation = (await client.post(path)).json()
    assert (await client.post(path + "/messages", json=payload)).status_code == 503
    oldest = (await client.get(path)).json()["attempts"][0]
    for index in range(12):
        request = {"client_message_id": f"later-finished-{index}", "content": "Unsent input preserved by the author"}
        async_session.add(SettingCollaborationAttempt(
            conversation_id=UUID(conversation["id"]), client_message_id=request["client_message_id"],
            request=request, request_fingerprint=sha256(SettingCollectionCollaborationSendRequest(**request).model_dump_json().encode()).hexdigest(),
            context={}, history=[], status="finished", created_at=datetime.now(UTC) + timedelta(seconds=index + 1),
        ))
    await async_session.commit()
    inspected = (await client.get(path)).json()
    assert len(inspected["attempts"]) == 11  # Ten recent + the old active request.
    assert next(item for item in inspected["attempts"] if item["id"] == oldest["id"])["status"] == "prepared"
    calls = []
    generated(monkeypatch, calls)
    if operation == "finish":
        response = await client.post(path + "/attempts/finish", json=payload)
        expected = "finished"
    else:
        response = await client.post(f"{path}/attempts/{oldest['id']}/resume")
        expected = "completed"
    assert response.status_code == 200, response.text
    explicit = next(item for item in response.json()["attempts"] if item["id"] == oldest["id"])
    assert explicit["status"] == expected
    assert len(response.json()["attempts"]) == 11
    assert len((await client.get(path)).json()["attempts"]) == 10
    assert (await client.get(f"{path}/attempts/{oldest['id']}")).json()["status"] == expected
    assert len(calls) == (1 if operation == "resume" else 0)


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_permission_revoked_after_dispatch_never_exposes_the_result_or_repeats_the_call(client, async_session, tmp_path, monkeypatch, scope):
    collection, path, payload = await seed(async_session, tmp_path, scope)
    original_owner, new_owner = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([original_owner, new_owner])
    await async_session.commit()
    collection.owner_id = original_owner.id
    await async_session.commit()
    old_headers, new_headers = {"x-actor-user-id": str(original_owner.id)}, {"x-actor-user-id": str(new_owner.id)}
    calls, started, finish = [], asyncio.Event(), asyncio.Event()
    generated(monkeypatch, calls, scope, started=started, finish=finish)
    await client.post(path, headers=old_headers)
    pending = asyncio.create_task(client.post(path + "/messages", json=payload, headers=old_headers))
    await asyncio.wait_for(started.wait(), timeout=3)
    try:
        attempt = (await client.get(path, headers=old_headers)).json()["attempts"][0]
        collection.owner_id = new_owner.id
        await async_session.commit()
    finally:
        finish.set()
    assert (await pending).status_code == 403
    assert (await client.get(path, headers=old_headers)).status_code == 403
    assert (await client.get(f"{path}/attempts/{attempt['id']}", headers=old_headers)).status_code == 403
    assert (await client.post(f"{path}/attempts/{attempt['id']}/resume", headers=old_headers)).status_code == 403
    assert (await client.post(path + "/messages", json=payload, headers=old_headers)).status_code == 403
    # The outcome is honestly unknown; even the current owner cannot regenerate it.
    recovered = (await client.post(path + "/messages", json=payload, headers=new_headers)).json()
    assert recovered["messages"] == []
    assert recovered["attempts"][0]["status"] == "dispatched"
    assert recovered["attempts"][0]["has_result"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("scope", ["collection", "document"])
async def test_finish_releases_scope_creation_locks_before_waiting_for_the_conversation(client, async_session, tmp_path, monkeypatch, scope):
    collection, path, payload = await seed(async_session, tmp_path, scope)
    conversation = (await client.post(path)).json()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    from app.services.setting_collaboration_service import SettingCollaborationService
    entered = asyncio.Event()
    original_get = SettingCollaborationService.get

    async def observed_get(service, *, lock=False):
        if lock:
            entered.set()
        return await original_get(service, lock=lock)

    monkeypatch.setattr(SettingCollaborationService, "get", observed_get)
    async with factory() as blocker, factory() as author:
        await blocker.scalar(select(SettingCollaboration).where(SettingCollaboration.id == UUID(conversation["id"])).with_for_update())
        pending = asyncio.create_task(client.post(path + "/attempts/finish", json=payload))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            # Adoption already owns the conversation row before it needs this
            # scope row. finish must not retain the inverse scope→conversation lock.
            model, identity = (SettingCollection, collection.id) if scope == "collection" else (Document, UUID(conversation["document_id"]))
            assert await author.scalar(select(model).where(model.id == identity).with_for_update(nowait=True)) is not None
        finally:
            await author.rollback()
            await blocker.rollback()
            result = await pending
        assert result.status_code == 200, result.text
        assert result.json()["attempts"][0]["status"] == "finished"
