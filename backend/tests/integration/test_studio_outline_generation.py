from uuid import uuid4

import pytest

from tests.integration import test_studio_restore_points as restore_points
from tests.integration import test_chapter_production_v2_routes_integration as production_routes
from tests.integration.test_studio_ideas import seed

client = restore_points.client
v2_client = production_routes.v2_client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_three_outline_candidates_survive_reload_selection_edit_and_approval(client, async_session, tmp_path):
    project, chapter, idea_url = await seed(async_session, tmp_path)
    idea = await client.put(idea_url, json={"request_id": str(uuid4()), "expected_revision": 0, "text": "调查档案失踪案，结尾留下新的线索。"})
    base = idea_url.removesuffix("/idea")
    payload = {"request_id": str(uuid4()), "expected_idea_revision": idea.json()["revision"]}
    generated = await client.post(f"{base}/generations", json=payload)
    assert generated.status_code == 201, generated.text
    batch = generated.json()
    assert batch["status"] == "completed"
    assert len(batch["candidates"]) == 3
    assert len({item["content"] for item in batch["candidates"]}) == 3
    assert (await client.post(f"{base}/generations", json=payload)).json() == batch
    assert (await client.get(f"{base}/generations/{payload['request_id']}")).json() == batch
    assert len((await client.get(f"{base}/generations")).json()) == 1
    select_payload = {"request_id": str(uuid4()), "candidate_id": batch["candidates"][1]["id"], "expected_current_version_id": None}
    selected = await client.post(f"{base}/generations/{payload['request_id']}/select", json=select_payload)
    assert selected.status_code == 200, selected.text
    selection = selected.json()
    assert (await client.post(f"{base}/generations/{payload['request_id']}/select", json=select_payload)).json() == selection
    assert (await client.get(f"/api/v1/documents/{selection['document_id']}/content")).json()["content"] == batch["candidates"][1]["content"]
    await async_session.refresh(chapter)
    assert chapter.current_draft_document_id is None
    assert chapter.approved_outline_version_id is None
    edited = await client.put(f"{base}/content", json={"content": "# 作者编辑后的大纲\n\n保留证据，追踪新的线索。", "expected_current_version_id": selection["version_id"]})
    assert edited.status_code == 200, edited.text
    approval = {"document_id": selection["document_id"], "expected_current_version_id": edited.json()["id"]}
    assert (await client.post(f"{base}/approve", json=approval)).status_code == 200
    assert (await client.post(f"{base}/generations/{payload['request_id']}/select", json=select_payload)).status_code == 409
    chapter.current_draft_document_id = uuid4()
    await async_session.commit()
    assert (await client.put(f"{base}/content", json={"content": "too late", "expected_current_version_id": edited.json()["id"]})).status_code == 409
    assert (await client.post(f"{base}/generations", json={**payload, "request_id": str(uuid4())})).status_code == 409


async def test_outline_generation_requires_current_nonempty_idea_and_exact_scope(client, async_session, tmp_path):
    project, chapter, idea_url = await seed(async_session, tmp_path)
    base = idea_url.removesuffix("/idea")
    payload = {"request_id": str(uuid4()), "expected_idea_revision": 0}
    assert (await client.post(f"{base}/generations", json=payload)).status_code == 409
    assert (await client.get(f"{base}/generations/{uuid4()}")).status_code == 404


async def test_uncertain_outline_request_is_never_blindly_reinvoked(client, async_session, tmp_path, monkeypatch):
    from app.llm.errors import ProviderTimeoutError
    from app.services import studio_outline_generation as generation
    calls = []

    async def uncertain(snapshot):
        calls.append(snapshot)
        raise ProviderTimeoutError()

    monkeypatch.setattr(generation, "generate_outline_options", uncertain)
    project, chapter, idea_url = await seed(async_session, tmp_path)
    await client.put(idea_url, json={"request_id": str(uuid4()), "expected_revision": 0, "text": "保留证据"})
    base = idea_url.removesuffix("/idea")
    payload = {"request_id": str(uuid4()), "expected_idea_revision": 1}
    first = (await client.post(f"{base}/generations", json=payload)).json()
    assert first["status"] == "uncertain"
    assert (await client.post(f"{base}/generations", json=payload)).json() == first
    assert len(calls) == 1
    assert (await client.post(f"{base}/generations", json={**payload, "request_id": str(uuid4())})).status_code == 409
    cancelled = await client.post(f"{base}/generations/{payload['request_id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert (await client.post(f"{base}/generations", json={**payload, "request_id": str(uuid4())})).json()["status"] == "uncertain"
    assert len(calls) == 2


async def test_generated_outline_starts_real_v2_pipeline_only_after_author_approval(v2_client, async_session, tmp_path):
    from app.api.deps import get_project_workspace
    from app.workspace import ProjectWorkspace
    client = v2_client
    client._transport.app.dependency_overrides[get_project_workspace] = lambda: ProjectWorkspace(tmp_path)
    project = await client.post("/api/v1/projects", json={"title": "From blank", "slug": f"blank-{uuid4()}"})
    assert project.status_code == 201, project.text
    from app.services.setting_context_resolver import SettingContextResolver
    from app.models import SettingCollection
    from uuid import UUID
    collection = await async_session.get(SettingCollection, UUID(project.json()["setting_collection_id"]))
    collection.revision = 7
    await async_session.commit()
    context = await SettingContextResolver(async_session).resolve_for_project(UUID(project.json()["id"]))
    assert context.collection_revision == 7 and context.documents == ()
    chapter = await client.post(f"/api/v1/projects/{project.json()['id']}/chapters", json={"title": "First chapter"})
    assert chapter.status_code == 201, chapter.text
    idea_url = f"/api/v1/projects/{project.json()['id']}/chapters/{chapter.json()['id']}/outline/idea"
    await client.put(idea_url, json={"request_id": str(uuid4()), "expected_revision": 0, "text": "调查档案中的矛盾，保留证据。"})
    base = idea_url.removesuffix("/idea")
    batch = (await client.post(f"{base}/generations", json={"request_id": str(uuid4()), "expected_idea_revision": 1})).json()
    selected = (await client.post(f"{base}/generations/{batch['id']}/select", json={"request_id": str(uuid4()),
        "candidate_id": batch["candidates"][0]["id"], "expected_current_version_id": None})).json()
    production = base.removesuffix("/outline") + "/production-v2"
    assert (await client.post(production)).status_code in (409, 422)
    assert (await client.post(f"{base}/approve", json={"document_id": selected["document_id"], "expected_current_version_id": selected["version_id"]})).status_code == 200
    started = await client.post(production)
    assert started.status_code == 201, started.text
    draft = await client.get(f"/api/v1/documents/{started.json()['draft_document_id']}/content")
    assert draft.status_code == 200
    assert draft.json()["content"].strip()
    run_url = f"{production}/{started.json()['workflow_run_id']}"
    accepted = await client.post(f"{run_url}/actions/{started.json()['action_request_id']}/resolve", json={
        "decision": "accept", "expected_current_version_id": draft.json()["version_id"]})
    assert accepted.status_code == 200, accepted.text
    for _ in range(5):
        state = (await client.get(run_url)).json()
        if state["status"] == "REVISION_READY":
            break
        reviewed = await client.post(f"{run_url}/review")
        assert reviewed.status_code == 200, reviewed.text
    else:
        pytest.fail("First chapter never became ready for local finalization")
    finalized = await client.post(f"{run_url}/finalize", json={"expected_current_version_id": state["document_version_id"]})
    assert finalized.status_code == 200, finalized.text
    assert (await client.get(run_url)).json()["status"] == "COMPLETED"
