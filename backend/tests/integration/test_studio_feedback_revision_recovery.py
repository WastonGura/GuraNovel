from uuid import UUID, uuid4

import pytest

from app.agents import DeterministicChapterWriterProvider
from app.agents.context_assembly import assemble_writer_context
from app.llm import ProviderTimeoutError
from tests.integration import test_chapter_production_v2_routes_integration as production

v2_client = production.v2_client
pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_feedback_revision_binds_outline_snapshot_and_recovers_provider_timeout(
    v2_client, async_session, tmp_path, monkeypatch,
):
    project, chapter, outline, outline_version, _ = await production._seed_approved_chapter(async_session, tmp_path)
    base = f"/api/v1/projects/{project.id}/chapters/{chapter.id}"
    started = (await v2_client.post(base + "/production-v2")).json()
    run_url = base + "/production-v2/" + started["workflow_run_id"]
    feedback_url = base + "/feedback/draft"
    written = await v2_client.put(feedback_url, json={"request_id": str(uuid4()), "expected_revision": 0,
        "expected_current_version_id": started["draft_version_id"], "comments": [], "requirements": "保留证据，调整对话。"})
    assert written.status_code == 200
    submitted = await v2_client.post(feedback_url + "/submissions", json={"request_id": str(uuid4()),
        "expected_revision": written.json()["revision"], "expected_current_version_id": started["draft_version_id"], "comment_ids": []})
    assert submitted.status_code == 200
    calls = []
    original = DeterministicChapterWriterProvider.revise_from_user_feedback

    async def strict_revision(self, request, profile):
        assembled = assemble_writer_context(request, profile)
        assert request.approved_outline.document_id == outline.id
        assert request.approved_outline.version_id == outline_version.id
        assert "Outline content describing key plot points." in assembled.outline_content
        calls.append(request)
        if len(calls) == 1:
            raise ProviderTimeoutError()
        return await original(self, request, profile)

    monkeypatch.setattr(DeterministicChapterWriterProvider, "revise_from_user_feedback", strict_revision)
    request = {"submission_id": submitted.json()["id"], "document_id": started["draft_document_id"],
        "version_id": started["draft_version_id"], "action_request_id": started["action_request_id"]}
    first = await v2_client.post(run_url + "/feedback-revisions", json=request)
    assert first.status_code == 503, first.text
    failed = await v2_client.get(run_url)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["document_version_id"] == started["draft_version_id"]
    revised = await v2_client.post(run_url + "/feedback-revisions", json=request)
    assert revised.status_code == 200, revised.text
    assert UUID(revised.json()["draft_version_id"]) != UUID(started["draft_version_id"])
    assert len(calls) == 2 and calls[0] == calls[1]
    assert (await v2_client.post(run_url + "/feedback-revisions", json=request)).json() == revised.json()
    assert len(calls) == 2
