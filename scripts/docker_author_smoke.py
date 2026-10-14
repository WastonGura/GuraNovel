"""Acceptance against a running disposable Docker stack, using its real HTTP API."""
import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def smoke(origin: str) -> dict:
    def call(method: str, path: str, payload=None, expected=(200, 201)):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(origin.rstrip('/') + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=180) as response:
                assert response.status in expected, f"Unexpected status: {method} {path}"
                return json.load(response)
        except HTTPError as error:
            if error.code in expected:
                return json.load(error)
            raise AssertionError(f"HTTP {error.code}: {method} {path}") from None

    call("GET", "/api/v1/health")
    project = call("POST", "/api/v1/projects", {"title": "Docker author acceptance", "slug": f"smoke-{uuid4().hex}"})
    chapter = call("POST", f"/api/v1/projects/{project['id']}/chapters", {"title": "The archive"})
    base = f"/api/v1/projects/{project['id']}/chapters/{chapter['id']}"
    idea = {"request_id": str(uuid4()), "expected_revision": 0, "text": "调查档案中的矛盾，保留证据。"}
    saved = call("PUT", base + "/outline/idea", idea)
    assert call("GET", base + "/outline/idea")["text"] == idea["text"]
    generation = {"request_id": str(uuid4()), "expected_idea_revision": saved["revision"]}
    batch = call("POST", base + "/outline/generations", generation)
    assert batch["status"] == "completed", f"Outline status: {batch['status']}"
    assert len(batch["candidates"]) == 3
    assert len({item["content"] for item in batch["candidates"]}) == 3
    assert call("POST", base + "/outline/generations", generation) == batch
    assert call("GET", base + f"/outline/generations/{batch['id']}") == batch
    selection = {"request_id": str(uuid4()), "candidate_id": batch["candidates"][0]["id"], "expected_current_version_id": None}
    selected = call("POST", base + f"/outline/generations/{batch['id']}/select", selection)
    assert call("POST", base + f"/outline/generations/{batch['id']}/select", selection) == selected
    call("POST", base + "/production-v2", expected=(409, 422))
    approval = {"document_id": selected["document_id"], "expected_current_version_id": selected["version_id"]}
    call("POST", base + "/outline/approve", approval)
    started = call("POST", base + "/production-v2")
    run = base + f"/production-v2/{started['workflow_run_id']}"
    draft = call("GET", f"/api/v1/documents/{started['draft_document_id']}/content")
    assert draft["content"].strip()
    edited = call("PUT", base + "/draft/content", {"content": draft["content"] + "\n\n作者确认保留证据。", "expected_current_version_id": draft["version_id"]})
    call("POST", run + f"/actions/{started['action_request_id']}/resolve", {"decision": "accept", "expected_current_version_id": edited["id"]})
    for _ in range(12):
        state = call("GET", run)
        if state["status"] == "REVISION_READY":
            break
        assert state["status"] != "FAILED", "Review failed"
        updated = call("POST", run + "/review")
        if updated.get("action_request_id"):
            call("POST", run + f"/actions/{updated['action_request_id']}/resolve", {"decision": "proceed_with_warnings"})
    else:
        raise AssertionError("Review did not become ready within 12 transitions")
    finalized = call("POST", run + "/finalize", {"expected_current_version_id": state["document_version_id"]})
    assert finalized["final_document_id"] and finalized["final_version_id"]
    assert call("POST", run + "/finalize", {"expected_current_version_id": state["document_version_id"]}) == finalized
    assert call("GET", run)["status"] == "COMPLETED"
    return {"project_id": project["id"], "chapter_id": chapter["id"], "generation_id": batch["id"],
            "workflow_run_id": started["workflow_run_id"], "final_version_id": finalized["final_version_id"],
            "provider": batch.get("provenance", {}).get("provider", "unknown"), "status": "COMPLETED"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:15283")
    args = parser.parse_args()
    print(json.dumps(smoke(args.origin)))
