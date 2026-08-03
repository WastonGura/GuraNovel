from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ChapterGenerationComposition, get_chapter_generation_composition, get_db_session
import app.api.routes_chapter_production as production_routes
from app.llm import (
    ChapterGenerationProvenance,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
    FakeChapterGenerationProvider,
    OpenAICompatibleChapterGenerationProvider,
    ProviderConfigurationError,
)
from app.llm.contracts import MAX_PROVENANCE_TOKEN_COUNT
from app.main import create_app
from app.models import WorkflowEvent
from app.services import ChapterProductionService, ChapterService, ProjectService
from app.workspace import ProjectWorkspace


@pytest.fixture
async def production_client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    def override_chapter_generation_composition() -> ChapterGenerationComposition:
        return ChapterGenerationComposition(
            FakeChapterGenerationProvider(),
            ChapterGenerationProvenance("fake", "deterministic-fake-v1", "chapter-production-v1"),
        )

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_chapter_generation_composition] = (
        override_chapter_generation_composition
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.integration
@pytest.mark.anyio
async def test_default_production_client_uses_fake_provider_without_network(
    production_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "default-fake")
    attempted_requests: list[httpx.Request] = []

    async def reject_network(
        _: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        attempted_requests.append(request)
        raise httpx.ConnectError("integration tests must not use network transport", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_network)

    started = await production_client.post(production_url(str(project.id), str(chapter.id)))

    assert started.status_code == 201, started.text
    assert started.json()["events"][1]["payload"] == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "chapter-production-v1",
    }
    assert attempted_requests == []


async def create_project_and_chapter(async_session: AsyncSession, workspace_base: Path):
    project = await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"production-routes-{workspace_base.name}", title="Archive of Ash"
    )
    chapter = await ChapterService(async_session).create_chapter(
        project_id=project.id, title="The Locked Door"
    )
    return project, chapter


def production_url(project_id: str, chapter_id: str) -> str:
    return f"/api/v1/projects/{project_id}/chapters/{chapter_id}/production-runs"


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_get_and_resolve_chapter_production_run(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "workspace")
    url = production_url(str(project.id), str(chapter.id))

    started = await production_client.post(url)

    assert started.status_code == 201
    started_body = started.json()
    assert started_body["type"] == "chapter_production"
    assert started_body["status"] == "awaiting_approval"
    assert started_body["current_node"] == "approval"
    assert started_body["next_node"] is None
    assert started_body["awaiting_user"] is True
    assert "workspace_root" not in started_body
    assert "content" not in str(started_body)
    assert len(started_body["actions"]) == 1
    action = started_body["actions"][0]
    assert action == {
        "id": action["id"],
        "type": "chapter_production_approval",
        "status": "pending",
        "options": ["approved", "rejected"],
        "default_option": "approved",
        "user_decision": None,
    }
    assert [event["event_type"] for event in started_body["events"]] == [
        "production_started",
        "generation_provenance",
        "generation_output_stored",
        "awaiting_approval",
    ]
    assert all(set(event) == {"event_type", "node_name", "message", "payload"} for event in started_body["events"])
    provenance = started_body["events"][1]["payload"]
    assert provenance == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "chapter-production-v1",
    }
    assert set(provenance) <= {
        "provider_kind",
        "model_identifier",
        "prompt_template_version",
        "input_tokens",
        "output_tokens",
    }
    assert started_body["outline_document_id"]
    assert started_body["draft_document_id"]

    fetched = await production_client.get(f"{url}/{started_body['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == started_body

    resolved = await production_client.post(
        f"{url}/{started_body['id']}/actions/{action['id']}/resolve", json={"decision": "approved"}
    )

    assert resolved.status_code == 200
    resolved_body = resolved.json()
    assert resolved_body["status"] == "completed"
    assert resolved_body["awaiting_user"] is False
    assert resolved_body["actions"][0]["status"] == "approved"
    assert resolved_body["actions"][0]["user_decision"] == "approved"
    assert [event["event_type"] for event in resolved_body["events"]] == [
        "production_started",
        "generation_provenance",
        "generation_output_stored",
        "awaiting_approval",
        "approval_approved",
    ]

    replay = await production_client.post(
        f"{url}/{started_body['id']}/actions/{action['id']}/resolve", json={"decision": "approved"}
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "workflow_state_error"


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_chapter_production_excludes_stored_event_secrets(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "event-secret")
    url = production_url(str(project.id), str(chapter.id))
    started = await production_client.post(url)
    provenance = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == UUID(started.json()["id"]),
            WorkflowEvent.event_type == "generation_provenance",
        )
    )
    assert provenance is not None
    provenance.payload = {**provenance.payload, "Authorization": "Bearer secret", "raw": "chapter"}
    await async_session.commit()

    fetched = await production_client.get(f"{url}/{started.json()['id']}")

    assert fetched.status_code == 200
    payload = fetched.json()["events"][1]["payload"]
    assert payload == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "chapter-production-v1",
    }
    assert "secret" not in str(fetched.json())


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "corrupt_payload", "opaque_secrets"),
    [
        *[
            pytest.param(
                f"{field}-opaque-secret",
                lambda payload, field=field, secret=f"sk-proj-{field}-opaque-secret": {
                    **payload,
                    field: secret,
                },
                (f"sk-proj-{field}-opaque-secret",),
                id=f"{field}-opaque-secret",
            )
            for field in ("provider_kind", "model_identifier", "prompt_template_version")
        ],
        *[
            pytest.param(
                f"{field}-invalid-whitespace-url",
                lambda payload, field=field: {**payload, field: "https://opaque.invalid/value with space"},
                ("https://opaque.invalid/value with space",),
                id=f"{field}-invalid-whitespace-url",
            )
            for field in ("provider_kind", "model_identifier", "prompt_template_version")
        ],
        *[
            pytest.param(
                f"{field}-non-string",
                lambda payload, field=field, secret=f"opaque-{field}-secret": {
                    **payload,
                    field: [secret],
                },
                (f"opaque-{field}-secret",),
                id=f"{field}-non-string",
            )
            for field in ("provider_kind", "model_identifier", "prompt_template_version")
        ],
        *[
            pytest.param(
                f"{field}-missing",
                lambda payload, field=field: {key: value for key, value in payload.items() if key != field},
                (),
                id=f"{field}-missing",
            )
            for field in ("provider_kind", "model_identifier", "prompt_template_version")
        ],
        *[
            pytest.param(
                f"{field}-bad-bool",
                lambda payload, field=field: {**payload, field: True},
                (),
                id=f"{field}-bad-bool",
            )
            for field in ("input_tokens", "output_tokens")
        ],
        *[
            pytest.param(
                f"{field}-bad-string",
                lambda payload, field=field, secret=f"opaque-{field}-secret": {
                    **payload,
                    field: secret,
                },
                (f"opaque-{field}-secret",),
                id=f"{field}-bad-string",
            )
            for field in ("input_tokens", "output_tokens")
        ],
        *[
            pytest.param(
                f"{field}-negative",
                lambda payload, field=field: {**payload, field: -1},
                (),
                id=f"{field}-negative",
            )
            for field in ("input_tokens", "output_tokens")
        ],
        *[
            pytest.param(
                f"{field}-over-limit",
                lambda payload, field=field: {**payload, field: MAX_PROVENANCE_TOKEN_COUNT + 1},
                (),
                id=f"{field}-over-limit",
            )
            for field in ("input_tokens", "output_tokens")
        ],
    ],
)
async def test_get_chapter_production_fail_closes_corrupt_stored_provenance(
    production_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    case: str,
    corrupt_payload: Callable[[dict[str, object]], dict[str, object]],
    opaque_secrets: tuple[str, ...],
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / case)
    url = production_url(str(project.id), str(chapter.id))
    started = await production_client.post(url)
    assert started.status_code == 201
    provenance = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == UUID(started.json()["id"]),
            WorkflowEvent.event_type == "generation_provenance",
        )
    )
    assert provenance is not None
    provenance.payload = corrupt_payload(dict(provenance.payload))
    await async_session.commit()

    fetched = await production_client.get(f"{url}/{started.json()['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["events"][1]["payload"] == {}
    assert all(secret not in fetched.text for secret in opaque_secrets)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "opaque_value",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "a" * 64,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvcGFxdWUtc2VjcmV0In0.signature",
    ],
)
async def test_api_malicious_provider_cannot_override_server_owned_provenance(
    production_client: httpx.AsyncClient,
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    opaque_value: str,
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "malicious-provider")

    class MaliciousProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            response = ChapterGenerationResponse(ChapterGenerationResult("outline", "draft", "summary"))
            for name in ("provenance", "provider_kind", "model_identifier", "prompt_template_version"):
                object.__setattr__(response, name, opaque_value)
            return response

    def trusted_service(
        session: AsyncSession, *_: object, **__: object
    ) -> ChapterProductionService:
        return ChapterProductionService(
            session, MaliciousProvider(),
            generation_provenance=ChapterGenerationProvenance("test", "server-test-model", "server-v1"),
        )

    monkeypatch.setattr(production_routes, "ChapterProductionService", trusted_service)
    url = production_url(str(project.id), str(chapter.id))
    started = await production_client.post(url)
    assert started.status_code == 201
    stored = await async_session.scalar(select(WorkflowEvent).where(
        WorkflowEvent.workflow_run_id == UUID(started.json()["id"]),
        WorkflowEvent.event_type == "generation_provenance",
    ))
    assert stored is not None
    expected = {
        "provider_kind": "test", "model_identifier": "server-test-model",
        "prompt_template_version": "server-v1",
    }
    assert stored.payload == expected
    fetched = await production_client.get(f"{url}/{started.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["events"][1]["payload"] == expected
    assert opaque_value not in str(stored.payload)
    assert opaque_value not in fetched.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_real_mode_uses_trusted_configuration_provenance(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "configured-provider")
    observed_headers: dict[str, str] = {}

    class IdentityRawStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield (
                b'{"choices":[{"message":{"content":"{\\"outline\\":\\"o\\",'
                b'\\"draft\\":\\"d\\",\\"summary\\":\\"s\\"}"}}],'
                b'"usage":{"prompt_tokens":5,"completion_tokens":8}}'
            )

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Encoding": "identity"},
            stream=IdentityRawStream(),
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleChapterGenerationProvider(
        base_url="https://fake-provider.test/v1",
        api_key="api-key-that-must-not-leak",
        model="vendor/model",
        client=upstream,
    )
    production_client._transport.app.dependency_overrides[get_chapter_generation_composition] = lambda: (  # type: ignore[attr-defined]
        ChapterGenerationComposition(
            provider,
            ChapterGenerationProvenance("openai_compatible", "vendor/model", "chapter-production-v1"),
        )
    )
    try:
        started = await production_client.post(production_url(str(project.id), str(chapter.id)))
    finally:
        await upstream.aclose()

    assert started.status_code == 201
    provenance = started.json()["events"][1]["payload"]
    assert provenance == {
        "provider_kind": "openai_compatible",
        "model_identifier": "vendor/model",
        "prompt_template_version": "chapter-production-v1",
        "input_tokens": 5,
        "output_tokens": 8,
    }
    output_event = started.json()["events"][2]
    assert output_event == {
        "event_type": "generation_output_stored",
        "node_name": "generate",
        "message": "Stored generated chapter output.",
        "payload": {"outline_document_id": started.json()["outline_document_id"]},
    }
    assert started.json()["events"][0]["message"] == "Started chapter production."
    assert all(
        "fake" not in event["message"].lower()
        and "deterministic" not in event["message"].lower()
        for event in started.json()["events"]
        if event["message"] is not None
    )
    assert observed_headers["authorization"] == "Bearer api-key-that-must-not-leak"
    assert "api-key-that-must-not-leak" not in started.text
    stored = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == UUID(started.json()["id"]),
            WorkflowEvent.event_type == "generation_provenance",
        )
    )
    assert stored is not None
    assert "api-key-that-must-not-leak" not in str(stored.payload)
    assert stored.payload == provenance


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_real_mode_misconfiguration_returns_safe_error_envelope(
    production_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_url = "socks5://proxy-user:proxy-password@proxy.test:1080"
    monkeypatch.setenv("ALL_PROXY", proxy_url)

    def misconfigured() -> ChapterGenerationComposition:
        try:
            raise ImportError(f"SOCKS support unavailable for {proxy_url}")
        except ImportError as error:
            raise ProviderConfigurationError() from error

    production_client._transport.app.dependency_overrides[get_chapter_generation_composition] = misconfigured  # type: ignore[attr-defined]
    response = await production_client.post(f"/api/v1/projects/{uuid4()}/chapters/{uuid4()}/production-runs")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "provider_configuration_error",
            "message": "The generation provider is not configured. Please contact the service operator.",
            "details": None,
        }
    }
    assert proxy_url not in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_chapter_production_routes_scope_ids_and_reject_invalid_input(
    production_client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "first")
    other_project, other_chapter = await create_project_and_chapter(async_session, tmp_path / "second")
    validation_project, validation_chapter = await create_project_and_chapter(
        async_session, tmp_path / "validation"
    )
    url = production_url(str(project.id), str(chapter.id))
    other_url = production_url(str(other_project.id), str(other_chapter.id))
    validation_url = production_url(str(validation_project.id), str(validation_chapter.id))
    started = await production_client.post(url, json={})
    other_started = await production_client.post(other_url, json={})
    assert started.status_code == other_started.status_code == 201
    run_id = started.json()["id"]
    action_id = started.json()["actions"][0]["id"]

    duplicate = await production_client.post(url, json={})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    for payload in (
        {"generated_content": "nope"},
        {"source": "agent"},
        {"project_id": str(uuid4())},
        {"chapter_id": str(uuid4())},
        {"workflow_run_id": str(uuid4())},
        {"status": "completed"},
        {"document_id": str(uuid4())},
        {"workspace_root": "/tmp/nope"},
        {"unexpected": True},
    ):
        response = await production_client.post(validation_url, json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    malformed = await production_client.get(f"{url}/not-a-uuid")
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "validation_error"

    for foreign_url in (
        f"{other_url}/{run_id}",
        f"{url}/{other_started.json()['id']}",
        f"{url}/{run_id}/actions/{other_started.json()['actions'][0]['id']}/resolve",
    ):
        response = (
            await production_client.post(foreign_url, json={"decision": "approved"})
            if "/resolve" in foreign_url
            else await production_client.get(foreign_url)
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    resolve_url = f"{url}/{run_id}/actions/{action_id}/resolve"
    for payload in (
        {},
        {"decision": "revise"},
        {"decision": "approved", "status": "completed"},
        {"decision": "approved", "document_id": str(uuid4())},
    ):
        response = await production_client.post(resolve_url, json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
