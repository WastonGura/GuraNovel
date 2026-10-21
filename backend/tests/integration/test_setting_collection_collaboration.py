"""The main setting Agent collaborates on the collection, even before any note exists."""
import asyncio
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
async def client(async_session):
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def seed(session, tmp_path):
    collection = SettingCollection(title="A whole setting collection", slug=str(uuid4()), workspace_root=str(tmp_path))
    session.add(collection)
    await session.commit()
    return collection, f"/api/v1/setting-collections/{collection.id}/collaboration"


async def test_empty_collection_opens_one_durable_main_agent_conversation_without_a_document(client, async_session, tmp_path):
    collection, path = await seed(async_session, tmp_path)
    response = await client.post(path)
    assert response.status_code == 200, response.text
    conversation = response.json()
    assert conversation["setting_collection_id"] == str(collection.id)
    assert conversation["document_id"] is None
    assert conversation["agent_role"] == "lore_agent"
    assert conversation["messages"] == []
    assert (await client.get(path)).json()["id"] == conversation["id"]
    assert (await client.post(path)).json()["id"] == conversation["id"]
    assert (await client.get(f"/api/v1/setting-collections/{collection.id}/documents")).json() == []


async def note(session, collection, title, *, world=False, body=None):
    return await DocumentService(session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW if world else DocumentType.CHARACTER_PROFILE,
        title=title, path=f"{'world' if world else 'setting'}/{uuid4()}.md", content=body or f"Original {title}.",
        source=DocumentSource.USER, metadata={"category": "world" if world else "setting"},
    )


def generated(monkeypatch, captured, *, target_title=None, invalid_target=None):
    from app.agents.setting_collaboration import SettingCollectionCollaborationReply, SettingCollectionSuggestedEdit

    async def reply(content, context, history):
        captured.append((content, context, history))
        target = next((doc for doc in context["documents"] if doc["title"] == target_title), None)
        return SettingCollectionCollaborationReply(reply="这是整个设定集的建议，尚未写入。", suggestion=SettingCollectionSuggestedEdit(
            target_document_id=invalid_target or (target["id"] if target else None),
            title="Model supplied title", category="world",
            proposed_content="    New complete prose with [[The world]].\n", reason="Make the collection internally consistent.",
        )), {"model_identifier": "synthetic-test-model", "prompt_template_version": "setting-collection-collaboration-v1"}

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", reply)


def acceptance(message):
    return {"expected_current_version_id": message["proposal"]["base_version_id"],
            "expected_draft_content": message["base_content"], "expected_title": message["base_title"]}


async def test_empty_collection_can_generate_a_new_entry_but_only_explicit_idempotent_adoption_creates_it(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    captured = []
    generated(monkeypatch, captured)
    await client.post(path)
    payload = {"client_message_id": "first-entry", "content": "Build a consistent world from scratch."}
    response = await client.post(path + "/messages", json=payload)
    assert response.status_code == 200, response.text
    message = response.json()["messages"][1]
    assert message["proposal"]["target_document_id"] is None
    assert message["proposal"]["base_version_id"] is None
    assert message["base_content"] == message["base_title"] == ""
    assert captured[0][1]["documents"] == []
    assert (await client.get(f"/api/v1/setting-collections/{collection.id}/documents")).json() == []
    assert (await client.post(path + "/messages", json=payload)).json() == response.json()
    assert len(captured) == 1
    assert (await client.post(path + "/messages", json={**payload, "content": "Changed request"})).status_code == 409
    url = f"{path}/proposals/{message['proposal']['id']}/accept"
    first, second = await asyncio.gather(client.post(url, json=acceptance(message)), client.post(url, json=acceptance(message)))
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    document = first.json()["document"]
    assert first.json()["conversation"]["document_id"] is None
    assert second.json()["document"]["id"] == document["id"]
    assert document["type"] == "world_overview"
    assert document["title"] == "Model supplied title"
    assert document["metadata"]["category"] == "world"
    assert first.json()["content"] == "    New complete prose with [[The world]].\n"
    assert len((await client.get(f"/api/v1/setting-collections/{collection.id}/documents")).json()) == 1
    assert len((await client.get(f"/api/v1/documents/{document['id']}/versions")).json()) == 1
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "accepted"


async def test_context_includes_all_active_documents_and_proposal_targets_the_server_bound_entry(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    world = await note(async_session, collection, "The world", world=True)
    character = await note(async_session, collection, "Character")
    deleted = await note(async_session, collection, "Old removed rule")
    assert (await client.delete(f"/api/v1/documents/{deleted.id}")).status_code == 204
    captured = []
    generated(monkeypatch, captured, target_title="Character")
    await client.post(path)
    response = await client.post(path + "/messages", json={"client_message_id": "collection-idea", "content": "Connect the protagonist with the world rules."})
    assert response.status_code == 200, response.text
    context = captured[0][1]
    assert context["scope"] == "collection"
    assert {doc["id"]: doc["body"] for doc in context["documents"]} == {str(world.id): "Original The world.", str(character.id): "Original Character."}
    message = response.json()["messages"][1]
    assert message["proposal"]["target_document_id"] == str(character.id)
    assert message["proposal"]["base_version_id"] == str(character.current_version_id)
    assert message["proposal"]["title"] == "Character"  # Model title/category cannot retarget an existing entry.
    assert message["proposal"]["category"] == "setting"
    assert message["base_title"] == "Character"
    assert message["base_content"] == "Original Character."
    accepted = await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["document"]["id"] == str(character.id)
    assert (await client.get(f"/api/v1/documents/{world.id}/content")).json()["content"] == "Original The world."
    assert (await client.post(path + "/messages", json={"client_message_id": "forged", "content": "Use a selected document", "document_id": str(world.id)})).status_code == 422
    old_conversation = (await client.post(f"/api/v1/setting-collections/{collection.id}/documents/{world.id}/collaboration")).json()
    gura = (await client.post(f"/api/v1/setting-collections/{collection.id}/assistant/conversations")).json()
    assert old_conversation["id"] != response.json()["id"] != gura["id"]
    assert old_conversation["messages"] == gura["messages"] == []


@pytest.mark.parametrize("document_type", [DocumentType.WORLD_OVERVIEW, DocumentType.POWER_SYSTEM, DocumentType.FACTIONS, DocumentType.GEOGRAPHY, DocumentType.HISTORY])
async def test_legacy_world_types_keep_their_category_without_metadata_in_both_agent_scopes(client, async_session, tmp_path, monkeypatch, document_type):
    collection, path = await seed(async_session, tmp_path)
    document = await DocumentService(async_session).create_document(
        setting_collection_id=collection.id, document_type=document_type, title="Legacy world entry",
        path=f"world/{uuid4()}.md", content="Original legacy rules.", source=DocumentSource.USER,
    )
    old_path = f"/api/v1/setting-collections/{collection.id}/documents/{document.id}/collaboration"
    assert (await client.post(old_path)).json()["agent_role"] == "worldbuilding_agent"
    captured = []
    generated(monkeypatch, captured, target_title="Legacy world entry")
    await client.post(path)
    response = await client.post(path + "/messages", json={"client_message_id": "legacy-world", "content": "Develop the existing world rules"})
    assert response.status_code == 200, response.text
    message = response.json()["messages"][1]
    assert captured[0][1]["documents"][0]["category"] == "world"
    assert message["proposal"]["category"] == "world"
    adopted = await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["document"]["type"] == document_type.value
    assert "category" not in (adopted.json()["document"]["metadata"] or {})


@pytest.mark.parametrize("change", ["target-body", "other-body", "target-title", "other-title", "delete-target", "delete-other", "add-entry"])
async def test_any_changed_collection_input_preserves_the_suggestion_instead_of_overwriting(client, async_session, tmp_path, monkeypatch, change):
    collection, path = await seed(async_session, tmp_path)
    target = await note(async_session, collection, "Target")
    other = await note(async_session, collection, "Other", world=True)
    generated(monkeypatch, [], target_title="Target")
    await client.post(path)
    message = (await client.post(path + "/messages", json={"client_message_id": "stale-idea", "content": "Improve the collection."})).json()["messages"][1]
    document = target if "target" in change else other
    if change.endswith("body"):
        assert (await client.put(f"/api/v1/documents/{document.id}/content", json={"content": "Author's newer text", "expected_current_version_id": str(document.current_version_id)})).status_code == 200
    elif change.endswith("title"):
        assert (await client.patch(f"/api/v1/documents/{document.id}", json={"title": "Author's newer title"})).status_code == 200
    elif change.startswith("delete"):
        assert (await client.delete(f"/api/v1/documents/{document.id}")).status_code == 204
    else:
        await note(async_session, collection, "Added while discussing")
    response = await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))
    assert response.status_code in (404, 409), response.text
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "pending"
    assert len((await client.get(f"/api/v1/documents/{target.id}/versions")).json()) == (2 if change == "target-body" else 1)


async def test_collection_author_can_keep_writing_while_the_agent_generates_against_its_exact_snapshot(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    target = await note(async_session, collection, "Target")
    other = await note(async_session, collection, "Other")
    from app.agents.setting_collaboration import SettingCollectionCollaborationReply, SettingCollectionSuggestedEdit
    started, finish = asyncio.Event(), asyncio.Event()
    contexts = []

    async def reply(content, context, history):
        contexts.append(context)
        started.set()
        await finish.wait()
        return SettingCollectionCollaborationReply(reply="A pending collection idea", suggestion=SettingCollectionSuggestedEdit(
            target_document_id=str(target.id), title="Ignored", category="setting", proposed_content="Agent suggestion", reason="Connect rules",
        )), {}

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", reply)
    await client.post(path)
    pending = asyncio.create_task(client.post(path + "/messages", json={"client_message_id": "writing-in-flight", "content": "Check all rules"}))
    await asyncio.wait_for(started.wait(), timeout=3)
    saved = await client.put(f"/api/v1/documents/{other.id}/content", json={"content": "Author continues", "expected_current_version_id": str(other.current_version_id)})
    assert saved.status_code == 200, saved.text
    finish.set()
    response = await pending
    assert next(doc for doc in contexts[0]["documents"] if doc["id"] == str(other.id))["body"] == "Original Other."
    message = response.json()["messages"][1]
    assert (await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))).status_code == 409


async def test_owner_archive_failure_retry_and_unknown_target_do_not_create_fake_outputs(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    collection.owner_id = owner.id
    await async_session.commit()
    assert (await client.post(path, headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
    await client.post(path)
    assert (await client.get(path, headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
    from app.llm.errors import ProviderUnavailableError

    async def fail(*args):
        raise ProviderUnavailableError()

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collection_collaboration", fail)
    payload = {"client_message_id": "retry-collection", "content": "Start the collection"}
    assert (await client.post(path + "/messages", json=payload)).status_code == 503
    assert (await client.get(path)).json()["messages"] == []
    captured = []
    generated(monkeypatch, captured, invalid_target=str(uuid4()))
    assert (await client.post(path + "/messages", json=payload)).status_code == 422
    assert (await client.get(path)).json()["messages"] == []
    generated(monkeypatch, captured)
    first, second = await asyncio.gather(client.post(path + "/messages", json=payload), client.post(path + "/messages", json=payload))
    assert first.status_code == second.status_code == 200
    assert len(captured) == 2  # One invalid provider target plus one acknowledged generation.
    message = first.json()["messages"][1]
    assert (await client.post(f"/api/v1/setting-collections/{collection.id}/archive")).status_code == 200
    assert (await client.get(path)).status_code == 200
    assert (await client.post(path + "/messages", json={**payload, "client_message_id": "new-after-archive"})).status_code == 409
    assert (await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))).status_code == 409
    assert (await client.post(f"{path}/proposals/{message['proposal']['id']}/discard")).status_code == 409


async def test_new_entry_suggestion_can_be_discarded_durably_without_creating_any_document(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    generated(monkeypatch, [])
    await client.post(path)
    message = (await client.post(path + "/messages", json={"client_message_id": "discard-new", "content": "Build an entry"})).json()["messages"][1]
    url = f"{path}/proposals/{message['proposal']['id']}"
    assert (await client.post(url + "/discard")).json()["messages"][1]["proposal_status"] == "rejected"
    assert (await client.post(url + "/discard")).status_code == 200
    assert (await client.post(url + "/accept", json=acceptance(message))).status_code == 409
    assert (await client.get(f"/api/v1/setting-collections/{collection.id}/documents")).json() == []


async def test_repeated_acceptance_after_a_later_edit_never_pairs_old_prose_with_the_new_version(client, async_session, tmp_path, monkeypatch):
    _, path = await seed(async_session, tmp_path)
    generated(monkeypatch, [])
    await client.post(path)
    message = (await client.post(path + "/messages", json={"client_message_id": "accept-then-edit", "content": "Create the first rule"})).json()["messages"][1]
    url = f"{path}/proposals/{message['proposal']['id']}/accept"
    adopted = (await client.post(url, json=acceptance(message))).json()
    document = adopted["document"]
    changed = await client.put(f"/api/v1/documents/{document['id']}/content", json={"content": "A later author's revision", "expected_current_version_id": document["current_version_id"]})
    assert changed.status_code == 200
    repeated = await client.post(url, json=acceptance(message))
    assert repeated.status_code == 409, repeated.text
    assert (await client.get(path)).json()["messages"][1]["accepted_version_id"] == document["current_version_id"]
    assert (await client.get(f"/api/v1/documents/{document['id']}/content")).json()["content"] == "A later author's revision"
    assert len((await client.get(f"/api/v1/documents/{document['id']}/versions")).json()) == 2


async def test_excessive_collection_context_is_rejected_before_provider_call_instead_of_silently_truncated(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    await note(async_session, collection, "A very large rule", body="x" * 1800001)
    captured = []
    generated(monkeypatch, captured)
    await client.post(path)
    response = await client.post(path + "/messages", json={"client_message_id": "oversized", "content": "Read all of it"})
    assert response.status_code == 422, response.text
    assert captured == []
    assert (await client.get(path)).json()["messages"] == []


async def test_renaming_the_collection_invalidates_a_suggestion_that_used_its_previous_identity(client, async_session, tmp_path, monkeypatch):
    collection, path = await seed(async_session, tmp_path)
    generated(monkeypatch, [])
    await client.post(path)
    message = (await client.post(path + "/messages", json={"client_message_id": "rename-collection", "content": "Develop the titled setting collection"})).json()["messages"][1]
    assert (await client.patch(f"/api/v1/setting-collections/{collection.id}", json={"title": "An author renamed this collection"})).status_code == 200
    response = await client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message))
    assert response.status_code == 409, response.text
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "pending"
    assert (await client.get(f"/api/v1/setting-collections/{collection.id}/documents")).json() == []


async def test_metadata_save_interleaving_with_adoption_preserves_the_author_edit_without_a_deadlock(client, async_session, tmp_path, monkeypatch):
    from sqlalchemy import event

    from app.services.setting_collaboration_service import SettingCollaborationService

    collection, path = await seed(async_session, tmp_path)
    target = await note(async_session, collection, "Target")
    generated(monkeypatch, [], target_title="Target")
    await client.post(path)
    message = (await client.post(path + "/messages", json={"client_message_id": "metadata-race", "content": "Improve the rule"})).json()["messages"][1]
    document_locked, metadata_locked = asyncio.Event(), asyncio.Event()
    database_errors = []
    original_collection = SettingCollaborationService._collection

    async def pause_after_document_lock(self, *, lock=False, writable=False, **kwargs):
        if lock and writable:
            document_locked.set()
            await asyncio.wait_for(metadata_locked.wait(), timeout=5)
        return await original_collection(self, lock=lock, writable=writable, **kwargs)

    def observe_metadata_lock(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("UPDATE setting_collections"):
            metadata_locked.set()

    def record_database_error(context):
        database_errors.append(getattr(context.original_exception, "sqlstate", None))

    monkeypatch.setattr(SettingCollaborationService, "_collection", pause_after_document_lock)
    engine = async_session.bind.sync_engine
    event.listen(engine, "after_cursor_execute", observe_metadata_lock)
    event.listen(engine, "handle_error", record_database_error)
    try:
        adoption = asyncio.create_task(client.post(f"{path}/proposals/{message['proposal']['id']}/accept", json=acceptance(message)))
        await asyncio.wait_for(document_locked.wait(), timeout=5)
        metadata = asyncio.create_task(client.patch(f"/api/v1/documents/{target.id}", json={"title": "Author's newer title"}))
        results = await asyncio.wait_for(asyncio.gather(adoption, metadata, return_exceptions=True), timeout=12)
    finally:
        event.remove(engine, "after_cursor_execute", observe_metadata_lock)
        event.remove(engine, "handle_error", record_database_error)
    assert database_errors == [], database_errors
    assert all(isinstance(result, httpx.Response) for result in results), [str(result)[:400] for result in results]
    assert [result.status_code for result in results] == [409, 200], [result.text for result in results]
    saved = (await client.get(f"/api/v1/documents/{target.id}")).json()
    assert saved["title"] == "Author's newer title"
    assert saved["current_version_id"] == str(target.current_version_id)
    assert len((await client.get(f"/api/v1/documents/{target.id}/versions")).json()) == 1
    assert (await client.get(f"/api/v1/documents/{target.id}/content")).json()["content"] == "Original Target."
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "pending"
