"""#65 HTTP integration regressions with an explicitly injected composition."""

from collections.abc import AsyncIterator
import logging
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.agents.chief_editor import ChiefEditor
from app.agents.composition import ProjectCreationComposition
from app.agents.concept_agent import ConceptAgent
from app.api.deps import get_db_session, get_project_creation_composition
from app.main import create_app
from app.services import ProjectService
from app.workspace import ProjectWorkspace
from app.models import ActionRequest, WorkflowCheckpoint, WorkflowEvent, WorkflowRun
from app.core.logging import APP_LOGGER_NAME


class Concepts:
    async def generate_concepts(self, request, profile):
        return {
            "options": [
                {
                    "id": "safe-option",
                    "title": "Safe Option",
                    "logline": "A safe logline.",
                    "premise": "A safe premise.",
                    "genres": ["fiction"],
                }
            ]
        }


class Clean:
    async def review_concepts(self, concepts, profile):
        return {"passed": True, "blocking_issues": [], "warnings": [], "notes": []}


def composition():
    return ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(Clean()))


class Blocking:
    async def review_concepts(self, concepts, profile):
        return {
            "passed": False,
            "summary": "unsafe report summary must stay private",
            "blocking_issues": [
                {"code": "premise_conflict", "message": "The premise conflicts with the requested tone."}
            ],
            "warnings": [],
            "notes": [],
        }


def blocking_composition():
    return ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(Blocking()))


@pytest.fixture
async def project_creation_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def db():
        yield async_session

    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_project_creation_composition] = composition
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def blocking_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def db():
        yield async_session

    app.dependency_overrides[get_db_session] = db
    app.dependency_overrides[get_project_creation_composition] = blocking_composition
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def project(session, root):
    return await ProjectService(session, ProjectWorkspace(root)).create_project(
        slug=f"route65-{root.name}", title="Route 65"
    )


def url(project_id):
    return f"/api/v1/projects/{project_id}/creation"


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_start_selection_foreign_and_replay(
    project_creation_client, async_session, tmp_path
):
    first, second = (
        await project(async_session, tmp_path / "one"),
        await project(async_session, tmp_path / "two"),
    )
    first_id, second_id = first.id, second.id
    started = await project_creation_client.post(
        f"{url(first_id)}/start", json={"user_seed": "secret seed"}
    )
    assert started.status_code == 201
    body = started.json()
    action = body["pending_action"]
    assert (
        body["status"] == "concept_options"
        and action["allowed_decisions"] == ["select", "fuse"]
        and action["concept_options"]
        == [
            {
                "id": "safe-option",
                "title": "Safe Option",
                "logline": "A safe logline.",
                "premise": "A safe premise.",
                "genres": ["fiction"],
            }
        ]
        and "secret seed" not in started.text
    )
    assert "concept_document_id" not in action and "concept_version_id" not in action
    assert (await project_creation_client.get(f"{url(second_id)}/{body['id']}")).status_code == 404
    assert (
        await project_creation_client.post(
            f"{url(first_id)}/{body['id']}/actions/{uuid4()}/resolve",
            json={"decision": "select", "option_id": "safe-option"},
        )
    ).status_code == 404
    resolved = await project_creation_client.post(
        f"{url(first_id)}/{body['id']}/actions/{action['id']}/resolve",
        json={"decision": "select", "option_id": "safe-option"},
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "concept_selected"
    assert (
        await project_creation_client.post(
            f"{url(first_id)}/{body['id']}/actions/{action['id']}/resolve",
            json={"decision": "select", "option_id": "safe-option"},
        )
    ).status_code == 409


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_validation_does_not_echo_seed(project_creation_client, async_session, tmp_path):
    created = await project(async_session, tmp_path)
    response = await project_creation_client.post(
        f"{url(created.id)}/start", json={"user_seed": "private", "unexpected": "leak"}
    )
    assert (
        response.status_code == 422
        and "private" not in response.text
        and "leak" not in response.text
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_start_accepts_public_maximum_transient_preferences(
    project_creation_client, async_session, tmp_path
):
    created = await project(async_session, tmp_path)
    seed = "s" * 4000
    platform = "p" * 500
    preferred_genre = "g" * 500
    disliked_element = "d" * 500
    style = "t" * 500

    response = await project_creation_client.post(
        f"{url(created.id)}/start",
        json={
            "user_seed": seed,
            "target_platform": platform,
            "preferred_genres": [preferred_genre],
            "disliked_elements": [disliked_element],
            "style_preference": style,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "concept_options"
    assert all(value not in response.text for value in (seed, platform, preferred_genre, disliked_element, style))


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_foreign_existing_action_does_not_mutate_legitimate_action(
    project_creation_client, async_session, tmp_path
):
    first, second = (
        await project(async_session, tmp_path / "first"),
        await project(async_session, tmp_path / "second"),
    )
    first_id, second_id = first.id, second.id
    one = (
        await project_creation_client.post(f"{url(first_id)}/start", json={"user_seed": "one"})
    ).json()
    two = (
        await project_creation_client.post(f"{url(second_id)}/start", json={"user_seed": "two"})
    ).json()
    response = await project_creation_client.post(
        f"{url(first_id)}/{one['id']}/actions/{two['pending_action']['id']}/resolve",
        json={"decision": "select", "option_id": "safe-option"},
    )
    assert response.status_code == 404
    assert (await project_creation_client.get(f"{url(first_id)}/{one['id']}")).json()[
        "pending_action"
    ]["id"] == one["pending_action"]["id"]


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse", "force_approved", "revise", "cancel"])
async def test_http_blocking_gate_rejects_illegal_decisions(
    blocking_client, async_session, tmp_path, decision
):
    created = await project(async_session, tmp_path / decision)
    body = (
        await blocking_client.post(f"{url(created.id)}/start", json={"user_seed": "seed"})
    ).json()
    action = body["pending_action"]
    assert action["allowed_decisions"] == ["regenerate", "feedback"]
    assert action["blocking_issues"] == [
        {"code": "premise_conflict", "message": "The premise conflicts with the requested tone."}
    ]
    assert set(action) == {
        "id", "type", "status", "allowed_decisions", "review_severity", "blocking_issues", "concept_options"
    }
    assert "unsafe report summary must stay private" not in body.__str__()
    assert [option["id"] for option in action["concept_options"]] == ["safe-option"]
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(m))
            for m in (ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    response = await blocking_client.post(
        f"{url(created.id)}/{body['id']}/actions/{action['id']}/resolve",
        json={"decision": decision, "option_id": "safe-option", "fused_concept": "x"},
    )
    assert response.status_code in {409, 422}
    stored = await async_session.get(ActionRequest, action["id"])
    run = await async_session.get(WorkflowRun, body["id"])
    assert stored.status == "pending" and run.status == "revision_required" and run.awaiting_user
    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(m))
            for m in (ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    assert "private review prose" not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_blocking_feedback_accepts_public_maximum_without_echo(
    blocking_client, async_session, tmp_path
):
    created = await project(async_session, tmp_path)
    started = await blocking_client.post(f"{url(created.id)}/start", json={"user_seed": "seed"})
    body = started.json()
    feedback = "f" * 1000

    response = await blocking_client.post(
        f"{url(created.id)}/{body['id']}/actions/{body['pending_action']['id']}/resolve",
        json={"decision": "feedback", "feedback": feedback},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "revision_required"
    assert feedback not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_corrupt_read_and_logs_are_safe(
    project_creation_client, async_session, tmp_path, caplog
):
    created = await project(async_session, tmp_path)
    caplog.set_level(logging.INFO, logger=APP_LOGGER_NAME)
    seed = "route-seed-secret"
    started = await project_creation_client.post(
        f"{url(created.id)}/start", json={"user_seed": seed}
    )
    body = started.json()
    read = await project_creation_client.get(f"{url(created.id)}/{body['id']}")
    assert seed not in started.text + read.text + caplog.text
    assert set(read.json()["pending_action"]) == {
        "id",
        "type",
        "status",
        "allowed_decisions",
        "review_severity",
        "blocking_issues",
        "concept_options",
    }
    assert set(read.json()["pending_action"]["concept_options"][0]) == {
        "id",
        "title",
        "logline",
        "premise",
        "genres",
    }
    run = await async_session.get(WorkflowRun, body["id"])
    run.status, run.metadata_ = "corrupt", {"secret": "corrupt-secret"}
    await async_session.commit()
    response = await project_creation_client.get(f"{url(created.id)}/{body['id']}")
    assert response.status_code == 409 and "corrupt-secret" not in response.text
