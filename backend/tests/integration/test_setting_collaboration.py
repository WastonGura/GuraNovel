"""A document's authoring Agent is durable and isolated from global Gura."""
import asyncio
from uuid import UUID, uuid4

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
    collection = SettingCollection(title="Tide archive", slug=str(uuid4()), workspace_root=str(tmp_path))
    session.add(collection)
    await session.commit()
    document = await DocumentService(session).create_document(
        setting_collection_id=collection.id, document_type=DocumentType.WORLD_OVERVIEW,
        title="The tide", path="world/tide.md", content="The tide turns at midnight.",
        source=DocumentSource.USER, metadata={"category": "world"},
    )
    return collection, document, f"/api/v1/setting-collections/{collection.id}/documents/{document.id}/collaboration"


async def test_document_authoring_conversation_is_durable_and_separate_from_gura(client, async_session, tmp_path):
    collection, document, path = await seed(async_session, tmp_path)
    response = await client.post(path)
    assert response.status_code == 200, response.text
    conversation = response.json()
    assert conversation["document_id"] == str(document.id)
    assert conversation["agent_role"] == "worldbuilding_agent"
    assert conversation["messages"] == []
    assert (await client.get(path)).json()["id"] == conversation["id"]
    assert (await client.post(path)).json()["id"] == conversation["id"]
    gura = (await client.post(f"/api/v1/setting-collections/{collection.id}/assistant/conversations")).json()
    assert gura["id"] != conversation["id"]
    assert gura["messages"] == []


def payload(document, **overrides):
    return {"client_message_id": str(uuid4()), "content": "Give the tide a cost.",
            "base_version_id": str(document.current_version_id), "draft_content": "The tide turns at midnight.", **overrides}


def suggestion(monkeypatch, captured):
    from app.agents.setting_collaboration import SettingCollaborationReply, SettingSuggestedEdit

    async def generate(content, context, history, *, before_dispatch):
        await before_dispatch({"model_identifier": "synthetic-contract-model"})
        captured.append((content, context, history))
        return SettingCollaborationReply(reply="可给潮汐增加代价，先比较再采纳。", suggestion=SettingSuggestedEdit(
            proposed_content=context["body"] + " Each reversal costs a memory.", reason="The cost supports the central conflict.",
        )), {"model_identifier": "synthetic-contract-model", "prompt_template_version": "setting-collaboration-v1"}

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collaboration", generate)


async def test_suggestions_do_not_write_and_lost_response_replay_keeps_one_model_call(client, async_session, tmp_path, monkeypatch):
    collection, document, path = await seed(async_session, tmp_path)
    captured = []
    suggestion(monkeypatch, captured)
    await client.post(path)
    request = payload(document, selection={"start": 0, "end": 8, "text": "The tide"})
    response = await client.post(path + "/messages", json=request)
    assert response.status_code == 200, response.text
    messages = response.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    proposal_message = messages[1]
    assert proposal_message["proposal_status"] == "pending"
    assert proposal_message["base_content"] == request["draft_content"]
    assert proposal_message["base_title"] == "The tide"
    assert proposal_message["proposal"]["source_task"]["agent_role"] == "worldbuilding_agent"
    assert proposal_message["proposal"]["base_version_id"] == request["base_version_id"]
    assert captured[0][1]["selection"] == request["selection"]
    assert (await client.get(path)).json()["messages"] == messages
    assert (await client.post(path + "/messages", json=request)).json()["messages"] == messages
    assert len(captured) == 1
    assert (await client.post(path + "/messages", json={**request, "content": "different"})).status_code == 409
    assert (await DocumentService(async_session).read_current_content(document.id)).content == request["draft_content"]
    await async_session.refresh(collection)
    assert collection.revision == 2


async def test_acceptance_commits_once_with_comments_references_and_pending_decision(client, async_session, tmp_path, monkeypatch):
    collection, document, path = await seed(async_session, tmp_path)
    document.metadata_ = {**document.metadata_, "comments": [{"id": "comment", "start": 0, "end": 8, "quote": "The tide", "text": "Keep this", "orphaned": False}]}
    await async_session.commit()
    suggestion(monkeypatch, [])
    await client.post(path)
    response = await client.post(path + "/messages", json=payload(document))
    proposal = response.json()["messages"][1]["proposal"]
    request = {"expected_current_version_id": str(document.current_version_id),
               "expected_draft_content": "The tide turns at midnight.", "expected_title": "The tide"}
    url = f"{path}/proposals/{proposal['id']}/accept"
    first, repeated = await asyncio.gather(client.post(url, json=request), client.post(url, json=request))
    assert first.status_code == repeated.status_code == 200, (first.text, repeated.text)
    accepted = first.json()
    version_id = accepted["document"]["current_version_id"]
    assert version_id != request["expected_current_version_id"]
    assert repeated.json()["document"]["current_version_id"] == version_id
    assert accepted["conversation"]["messages"][1]["proposal_status"] == "accepted"
    assert accepted["conversation"]["messages"][1]["accepted_version_id"] == version_id
    assert accepted["document"]["metadata"]["comments"][0]["orphaned"] is False
    assert accepted["document"]["metadata"]["comments_source_version_id"] == version_id
    assert accepted["content"].endswith("Each reversal costs a memory.")
    versions = (await client.get(f"/api/v1/documents/{document.id}/versions")).json()
    assert len(versions) == 2
    assert versions[-1]["agent_role"] == "worldbuilding_agent"
    assert (await client.post(f"{path}/proposals/{proposal['id']}/discard")).status_code == 409
    await async_session.refresh(collection)
    assert collection.revision == 3


async def test_author_typing_during_model_response_preserves_both_and_blocks_stale_adoption(client, async_session, tmp_path, monkeypatch):
    _, document, path = await seed(async_session, tmp_path)
    from app.agents.setting_collaboration import SettingCollaborationReply, SettingSuggestedEdit
    started, resume = asyncio.Event(), asyncio.Event()

    async def generate(*args, before_dispatch):
        await before_dispatch({"model_identifier": "synthetic-contract-model"})
        started.set()
        await resume.wait()
        return SettingCollaborationReply(reply="A suggestion", suggestion=SettingSuggestedEdit(proposed_content="Agent version", reason="Try this")), {}

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collaboration", generate)
    await client.post(path)
    request = payload(document)
    pending = asyncio.create_task(client.post(path + "/messages", json=request))
    await asyncio.wait_for(started.wait(), timeout=3)
    saved = await client.put(f"/api/v1/documents/{document.id}/content", json={"expected_current_version_id": request["base_version_id"], "content": "Author's newer text"})
    assert saved.status_code == 200, saved.text
    resume.set()
    response = await pending
    proposal = response.json()["messages"][1]["proposal"]
    result = await client.post(f"{path}/proposals/{proposal['id']}/accept", json={
        "expected_current_version_id": request["base_version_id"], "expected_draft_content": request["draft_content"], "expected_title": "The tide",
    })
    assert result.status_code == 409
    assert (await client.get(f"/api/v1/documents/{document.id}/content")).json()["content"] == "Author's newer text"
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "pending"
    assert (await client.get(path)).json()["messages"][1]["proposal"]["proposed_content"] == "Agent version"


async def test_changed_title_or_local_draft_cannot_be_overwritten_and_discard_is_durable(client, async_session, tmp_path, monkeypatch):
    _, document, path = await seed(async_session, tmp_path)
    suggestion(monkeypatch, [])
    await client.post(path)
    proposal = (await client.post(path + "/messages", json=payload(document))).json()["messages"][1]["proposal"]
    url = f"{path}/proposals/{proposal['id']}"
    acceptance = {"expected_current_version_id": str(document.current_version_id), "expected_draft_content": "New unsaved draft", "expected_title": "The tide"}
    assert (await client.post(url + "/accept", json=acceptance)).status_code == 409
    await client.patch(f"/api/v1/documents/{document.id}", json={"title": "New title"})
    acceptance["expected_draft_content"] = "The tide turns at midnight."
    assert (await client.post(url + "/accept", json=acceptance)).status_code == 409
    discarded = await client.post(url + "/discard")
    assert discarded.status_code == 200, discarded.text
    assert (await client.post(url + "/discard")).json()["messages"][1]["proposal_status"] == "rejected"
    assert (await client.post(url + "/accept", json=acceptance)).status_code == 409
    assert len((await client.get(f"/api/v1/documents/{document.id}/versions")).json()) == 1


async def test_scope_permissions_archive_and_failed_model_retries(client, async_session, tmp_path, monkeypatch):
    collection, document, path = await seed(async_session, tmp_path)
    owner, stranger = User(username=str(uuid4())), User(username=str(uuid4()))
    async_session.add_all([owner, stranger])
    await async_session.commit()
    collection.owner_id = owner.id
    await async_session.commit()
    assert (await client.post(path, headers={"x-actor-user-id": str(stranger.id)})).status_code == 403
    await client.post(path)
    wrong = path.replace(str(collection.id), str(uuid4()))
    assert (await client.get(wrong)).status_code == 404
    from app.llm.errors import ProviderUnavailableError

    async def fail(*args, before_dispatch):
        await before_dispatch({"model_identifier": "synthetic-contract-model"})
        raise ProviderUnavailableError()

    monkeypatch.setattr("app.agents.setting_collaboration.generate_setting_collaboration", fail)
    request = payload(document)
    assert (await client.post(path + "/messages", json=request)).status_code == 503
    assert (await client.get(path)).json()["messages"] == []
    captured = []
    suggestion(monkeypatch, captured)
    assert (await client.post(path + "/messages", json=request)).json()["attempts"][0]["status"] == "dispatched"
    assert captured == []
    assert (await client.post(path + "/attempts/finish", json=request)).status_code == 200
    request = {**request, "client_message_id": str(uuid4())}
    first, second = await asyncio.gather(client.post(path + "/messages", json=request), client.post(path + "/messages", json=request))
    assert first.status_code == second.status_code == 200
    assert len(captured) == 1
    completed = next(response for response in (first, second) if response.json()["messages"])
    proposal = completed.json()["messages"][1]["proposal"]
    collection.status = "archived"
    await async_session.commit()
    assert (await client.get(path)).status_code == 200
    assert (await client.post(path + "/messages", json=payload(document))).status_code == 409
    assert (await client.post(f"{path}/proposals/{proposal['id']}/discard")).status_code == 409
    assert (await client.post(f"{path}/proposals/{proposal['id']}/accept", json={"expected_current_version_id": str(document.current_version_id), "expected_draft_content": request["draft_content"], "expected_title": "The tide"})).status_code == 409


async def test_creation_identity_reconciles_lost_response_without_duplicating_documents(client, async_session, tmp_path):
    collection, _, _ = await seed(async_session, tmp_path)
    path = f"/api/v1/setting-collections/{collection.id}/documents"
    request = {"type": "character_profile", "title": "A new voice", "path": f"setting/{uuid4()}.md", "content": "A first sentence", "client_request_id": str(uuid4()), "metadata": {"category": "setting", "comments": []}}
    first, second = await asyncio.gather(client.post(path, json=request), client.post(path, json=request))
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["metadata"]["__creation_request_id"] == request["client_request_id"]
    assert (await client.post(path, json={**request, "content": "different"})).status_code == 409
    assert len((await client.get(path)).json()) == 2
    await async_session.refresh(collection)
    assert collection.revision == 3


@pytest.mark.parametrize("external_edit", ["body", "title"])
async def test_creation_replay_never_pairs_an_external_newer_version_with_the_original_draft(client, async_session, tmp_path, external_edit):
    collection, _, _ = await seed(async_session, tmp_path)
    path = f"/api/v1/setting-collections/{collection.id}/documents"
    request = {"type": "character_profile", "title": "Original title", "path": f"setting/{uuid4()}.md", "content": "First snapshot", "client_request_id": str(uuid4())}
    document = (await client.post(path, json=request)).json()
    if external_edit == "body":
        saved = await client.put(f"/api/v1/documents/{document['id']}/content", json={"expected_current_version_id": document["current_version_id"], "content": "Other writer's new text"})
    else:
        saved = await client.patch(f"/api/v1/documents/{document['id']}", json={"title": "Other writer's new title"})
    assert saved.status_code == 200
    replay = await client.post(path, json=request)
    assert replay.status_code == 409, replay.text
    assert len((await client.get(path)).json()) == 2
    if external_edit == "body":
        assert (await client.get(f"/api/v1/documents/{document['id']}/content")).json()["content"] == "Other writer's new text"
    else:
        assert (await client.get(f"/api/v1/documents/{document['id']}")).json()["title"] == "Other writer's new title"


async def test_acceptance_refreshes_archival_status_even_when_the_session_cached_active_collection(client, async_session, tmp_path, monkeypatch):
    from app.api.schemas_setting_collaboration import SettingProposalAcceptanceRequest
    from app.core.errors import ConflictError
    from app.services.setting_collaboration_service import SettingCollaborationService

    collection, document, path = await seed(async_session, tmp_path)
    suggestion(monkeypatch, [])
    await client.post(path)
    proposal = (await client.post(path + "/messages", json=payload(document))).json()["messages"][1]["proposal"]
    # This session has already read active status; another request archives it.
    assert collection.status == "active"
    assert (await client.post(f"/api/v1/setting-collections/{collection.id}/archive")).status_code == 200
    service = SettingCollaborationService(async_session, collection.id, document.id)
    document_id = document.id
    with pytest.raises(ConflictError, match="Archived"):
        await service.accept(UUID(proposal["id"]), SettingProposalAcceptanceRequest(
            expected_current_version_id=document.current_version_id,
            expected_draft_content="The tide turns at midnight.", expected_title="The tide",
        ))
    await async_session.rollback()
    assert (await client.get(path)).json()["messages"][1]["proposal_status"] == "pending"
    assert (await client.get(f"/api/v1/documents/{document_id}/content")).json()["content"] == "The tide turns at midnight."
