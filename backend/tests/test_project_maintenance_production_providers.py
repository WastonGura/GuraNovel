"""Unit tests for OpenAI-compatible project maintenance agents and providers."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.agents.maintenance_agents import (
    ArchivistAgent,
    ChiefEditorAgent,
    LoreAgent,
    PlotArchitectAgent,
)
from app.agents.maintenance_contracts import (
    AffectedItemReference,
    AffectedItemType,
    AppliedDocumentReference,
    ApplyChangeOutput,
    ApplyChangeRequest,
    ChiefEditorMaintenanceImpactOutput,
    ConsistencyReviewOutput,
    DocumentVersionReference,
    ImpactLevel,
    LoreImpactOutput,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionOperation,
    RevisionOperationKind,
    RevisionPlanOutput,
    RevisionPlanRequest,
)
from app.agents.maintenance_providers import OpenAICompatibleMaintenanceProvider
from app.agents.profiles import ProfileRegistry
from app.api.deps import get_project_maintenance_composition
from app.core.config import Settings
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def _make_mock_client(
    handler_or_payload: Any,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    recorded_requests: list[dict[str, Any]] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        recorded_requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": body,
            }
        )
        if callable(handler_or_payload):
            return handler_or_payload(request, body)

        if isinstance(handler_or_payload, Exception):
            raise handler_or_payload

        response_body = {
            "id": "chatcmpl-mock-maintenance",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            json.dumps(handler_or_payload)
                            if isinstance(handler_or_payload, dict)
                            else str(handler_or_payload)
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 120,
                "total_tokens": 270,
            },
        }
        resp_headers = headers or {"content-type": "application/json", "content-encoding": "identity"}
        payload_bytes = json.dumps(response_body).encode("utf-8")
        return httpx.Response(
            status_code=status_code,
            headers=resp_headers,
            stream=_CountingStream([payload_bytes]),
        )

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport, base_url="https://mock-provider.test/v1/")
    return client, recorded_requests


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "project_creation_provider": "fake",
        "project_maintenance_provider": "fake",
        "chapter_generation_provider": "fake",
        "chapter_production_provider": "fake",
        "reader_panel_mode": "off",
        "openai_compatible_base_url": None,
        "openai_compatible_api_key": None,
        "openai_compatible_model": None,
        "openai_compatible_timeout_seconds": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def ids() -> dict[str, UUID]:
    return {
        "project_id": uuid4(),
        "workflow_run_id": uuid4(),
        "change_request_id": uuid4(),
        "document_id": uuid4(),
        "version_id": uuid4(),
        "affected_item_id": uuid4(),
        "approval_id": uuid4(),
        "revision_plan_id": uuid4(),
        "revision_plan_document_id": uuid4(),
        "revision_plan_version_id": uuid4(),
        "operation_id": uuid4(),
        "change_set_id": uuid4(),
    }


class TestOpenAICompatibleMaintenanceProvider:
    @pytest.mark.anyio
    async def test_lore_impact_analysis_success(self, ids: dict[str, UUID]) -> None:
        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        request = MaintenanceImpactRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Expand arcane academy historical timeline.",
            document_refs=(doc_ref,),
        )
        payload = {
            "affected_items": [
                {
                    "stable_reference": "world/arcane-academy-history",
                    "item_type": "world",
                    "impact_level": "medium",
                    "document": {
                        "document_id": str(ids["document_id"]),
                        "current_version_id": str(ids["version_id"]),
                    },
                    "reason": "Expands academy founding era canon.",
                }
            ],
            "impact_summary": "The change affects core academy world lore.",
            "required_rewrites": [],
            "safe_to_change": True,
            "warnings": [],
        }
        client, recorded = _make_mock_client(payload)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-maint-model",
            client=client,
        )
        registry = ProfileRegistry()
        agent = LoreAgent(provider, registry=registry)

        try:
            result = await agent.analyze(request)
            assert isinstance(result, LoreImpactOutput)
            assert result.safe_to_change is True
            assert len(result.affected_items) == 1
            assert result.affected_items[0].stable_reference == "world/arcane-academy-history"
            assert provider.last_provenance is not None
            assert provider.last_input_tokens == 150
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_chief_editor_impact_analysis_success(self, ids: dict[str, UUID]) -> None:
        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        request = MaintenanceImpactRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Shift main protagonist relationship from rivals to siblings.",
            document_refs=(doc_ref,),
        )
        payload = {
            "affected_items": [
                {
                    "stable_reference": "character/protagonist-relationship",
                    "item_type": "character",
                    "impact_level": "high",
                    "document": {
                        "document_id": str(ids["document_id"]),
                        "current_version_id": str(ids["version_id"]),
                    },
                    "reason": "Fundamental character dynamics shift alters reader expectations.",
                }
            ],
            "impact_summary": "Major narrative and commercial implications for core audience.",
            "required_rewrites": [],
            "safe_to_change": True,
            "warnings": [],
            "reader_expectation_impact": "high",
            "commercial_impact": "high",
        }
        client, recorded = _make_mock_client(payload)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-maint-model",
            client=client,
        )
        registry = ProfileRegistry()
        agent = ChiefEditorAgent(provider, registry=registry)

        try:
            result = await agent.analyze(request)
            assert isinstance(result, ChiefEditorMaintenanceImpactOutput)
            assert result.safe_to_change is True
            assert result.reader_expectation_impact == ImpactLevel.HIGH
            assert result.commercial_impact == ImpactLevel.HIGH
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_revision_plan_success(self, ids: dict[str, UUID]) -> None:
        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        affected_item = AffectedItemReference(
            affected_item_id=ids["affected_item_id"],
            stable_reference="outline/heist-reveal",
            item_type=AffectedItemType.OUTLINE,
            impact_level=ImpactLevel.MEDIUM,
            document=doc_ref,
            reason="Heist climax timing must match new timeline.",
        )
        request = RevisionPlanRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Adjust heist reveal sequence.",
            affected_items=(affected_item,),
            document_refs=(doc_ref,),
        )
        payload = {
            "plan_id": str(ids["revision_plan_id"]),
            "summary": "Execute ordered revision of chapter 1 document.",
            "operations": [
                {
                    "operation_id": str(ids["operation_id"]),
                    "sequence": 1,
                    "operation": "revise",
                    "target": {
                        "document_id": str(ids["document_id"]),
                        "current_version_id": str(ids["version_id"]),
                    },
                    "affected_item_ids": [str(ids["affected_item_id"])],
                    "instruction": "Shift the reveal scene to chapter end.",
                }
            ],
            "safety": {
                "requires_user_confirmation": True,
                "preserve_existing_versions": True,
                "direct_write_authority": False,
            },
            "warnings": [],
        }
        client, recorded = _make_mock_client(payload)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-maint-model",
            client=client,
        )
        registry = ProfileRegistry()
        agent = PlotArchitectAgent(provider, registry=registry)

        try:
            result = await agent.plan(request)
            assert isinstance(result, RevisionPlanOutput)
            assert result.plan_id == ids["revision_plan_id"]
            assert len(result.operations) == 1
            assert result.operations[0].sequence == 1
            assert result.safety.requires_user_confirmation is True
            assert result.safety.direct_write_authority is False
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_archivist_apply_change_success(self, ids: dict[str, UUID]) -> None:
        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        operation = RevisionOperation(
            operation_id=ids["operation_id"],
            sequence=1,
            operation=RevisionOperationKind.REVISE,
            target=doc_ref,
            affected_item_ids=(ids["affected_item_id"],),
            instruction="Update timeline events in chronicle document.",
        )
        request = ApplyChangeRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            approval_id=ids["approval_id"],
            revision_plan_id=ids["revision_plan_id"],
            revision_plan_document_id=ids["revision_plan_document_id"],
            revision_plan_version_id=ids["revision_plan_version_id"],
            operations=(operation,),
        )
        edit_id = uuid4()
        payload = {
            "change_set_id": str(ids["change_set_id"]),
            "project_id": str(ids["project_id"]),
            "workflow_run_id": str(ids["workflow_run_id"]),
            "change_request_id": str(ids["change_request_id"]),
            "approval_id": str(ids["approval_id"]),
            "revision_plan_id": str(ids["revision_plan_id"]),
            "revision_plan_document_id": str(ids["revision_plan_document_id"]),
            "revision_plan_version_id": str(ids["revision_plan_version_id"]),
            "proposed_edits": [
                {
                    "proposed_edit_id": str(edit_id),
                    "sequence": 1,
                    "project_id": str(ids["project_id"]),
                    "workflow_run_id": str(ids["workflow_run_id"]),
                    "change_request_id": str(ids["change_request_id"]),
                    "approval_id": str(ids["approval_id"]),
                    "revision_plan_id": str(ids["revision_plan_id"]),
                    "revision_plan_document_id": str(ids["revision_plan_document_id"]),
                    "revision_plan_version_id": str(ids["revision_plan_version_id"]),
                    "revision_operation_id": str(ids["operation_id"]),
                    "document_id": str(ids["document_id"]),
                    "expected_current_version_id": str(ids["version_id"]),
                    "operation": "replace_content",
                    "content": "# Updated Chronicle\n\nNew historical events recorded.",
                    "rationale": "Incorporate timeline revisions into official chronicle.",
                }
            ],
        }
        client, recorded = _make_mock_client(payload)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-maint-model",
            client=client,
        )
        registry = ProfileRegistry()
        agent = ArchivistAgent(provider, registry=registry)

        try:
            result = await agent.apply_change(request)
            assert isinstance(result, ApplyChangeOutput)
            assert result.change_set_id == ids["change_set_id"]
            assert len(result.proposed_edits) == 1
            edit = result.proposed_edits[0]
            assert edit.document_id == ids["document_id"]
            assert edit.operation.value == "replace_content"
            assert "Updated Chronicle" in edit.content
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_post_change_consistency_review_clean(self, ids: dict[str, UUID]) -> None:
        new_version_id = uuid4()
        applied_change = AppliedDocumentReference(
            proposed_edit_id=uuid4(),
            document_id=ids["document_id"],
            previous_version_id=ids["version_id"],
            current_version_id=new_version_id,
        )
        request = PostChangeRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            approval_id=ids["approval_id"],
            revision_plan_id=ids["revision_plan_id"],
            revision_plan_document_id=ids["revision_plan_document_id"],
            revision_plan_version_id=ids["revision_plan_version_id"],
            change_set_id=ids["change_set_id"],
            applied_changes=(applied_change,),
        )
        review_id = uuid4()
        payload = {
            "review_id": str(review_id),
            "project_id": str(ids["project_id"]),
            "workflow_run_id": str(ids["workflow_run_id"]),
            "change_request_id": str(ids["change_request_id"]),
            "approval_id": str(ids["approval_id"]),
            "revision_plan_id": str(ids["revision_plan_id"]),
            "revision_plan_document_id": str(ids["revision_plan_document_id"]),
            "revision_plan_version_id": str(ids["revision_plan_version_id"]),
            "change_set_id": str(ids["change_set_id"]),
            "outcome": "clean",
            "findings": [],
        }
        client, recorded = _make_mock_client(payload)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            model="test-maint-model",
            client=client,
        )
        registry = ProfileRegistry()
        agent = LoreAgent(provider, registry=registry)

        try:
            result = await agent.post_change(request)
            assert isinstance(result, ConsistencyReviewOutput)
            assert result.review_id == review_id
            assert result.outcome.value == "clean"
            assert len(result.findings) == 0
            assert len(recorded) == 1
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_maintenance_impact_rate_limited(self, ids: dict[str, UUID]) -> None:
        client, _ = _make_mock_client({"error": "rate limit"}, status_code=429)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        agent = LoreAgent(provider, registry=registry)

        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        request = MaintenanceImpactRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Expand lore timeline.",
            document_refs=(doc_ref,),
        )
        try:
            with pytest.raises(ProviderRateLimitedError):
                await agent.analyze(request)
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_maintenance_impact_server_unavailable(self, ids: dict[str, UUID]) -> None:
        client, _ = _make_mock_client({"error": "server error"}, status_code=500)
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        agent = LoreAgent(provider, registry=registry)

        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        request = MaintenanceImpactRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Expand lore timeline.",
            document_refs=(doc_ref,),
        )
        try:
            with pytest.raises(ProviderUnavailableError):
                await agent.analyze(request)
        finally:
            await provider.aclose()

    @pytest.mark.anyio
    async def test_maintenance_impact_timeout(self, ids: dict[str, UUID]) -> None:
        client, _ = _make_mock_client(httpx.ReadTimeout("Request timed out"))
        provider = OpenAICompatibleMaintenanceProvider(
            base_url="https://mock-provider.test/v1",
            api_key="test-api-key",
            client=client,
        )
        registry = ProfileRegistry()
        agent = LoreAgent(provider, registry=registry)

        doc_ref = DocumentVersionReference(
            document_id=ids["document_id"], current_version_id=ids["version_id"]
        )
        request = MaintenanceImpactRequest(
            project_id=ids["project_id"],
            workflow_run_id=ids["workflow_run_id"],
            change_request_id=ids["change_request_id"],
            change_request="Expand lore timeline.",
            document_refs=(doc_ref,),
        )
        try:
            with pytest.raises(ProviderTimeoutError):
                await agent.analyze(request)
        finally:
            await provider.aclose()


class TestProjectMaintenanceCompositionDependencyInjection:
    def test_default_fake_provider(self) -> None:
        config = make_settings(project_maintenance_provider="fake")
        comp = get_project_maintenance_composition(configured_settings=config)
        assert comp is not None
        assert comp.lore_agent is not None
        assert comp.chief_editor_agent is not None
        assert comp.plot_architect_agent is not None
        assert comp.worldbuilding_agent is not None
        assert comp.archivist_agent is not None

    def test_configured_openai_compatible_provider(self) -> None:
        config = make_settings(
            project_maintenance_provider="openai_compatible",
            openai_compatible_base_url="http://localhost:8000/v1",
            openai_compatible_api_key=SecretStr("test-key"),
            openai_compatible_model="test-model",
            openai_compatible_timeout_seconds=30.0,
        )
        comp = get_project_maintenance_composition(configured_settings=config)
        assert comp is not None
        assert isinstance(comp.lore_agent._provider, OpenAICompatibleMaintenanceProvider)
        assert isinstance(comp.chief_editor_agent._provider, OpenAICompatibleMaintenanceProvider)
        assert isinstance(comp.plot_architect_agent._provider, OpenAICompatibleMaintenanceProvider)
        assert isinstance(comp.worldbuilding_agent._provider, OpenAICompatibleMaintenanceProvider)
        assert isinstance(comp.archivist_agent._provider, OpenAICompatibleMaintenanceProvider)

    @pytest.mark.parametrize(
        "missing_kwargs",
        [
            {"openai_compatible_base_url": None},
            {"openai_compatible_api_key": None},
            {"openai_compatible_model": None},
            {"openai_compatible_timeout_seconds": None},
        ],
    )
    def test_openai_compatible_incomplete_config_raises(self, missing_kwargs: dict[str, Any]) -> None:
        base = {
            "project_maintenance_provider": "openai_compatible",
            "openai_compatible_base_url": "http://localhost:8000/v1",
            "openai_compatible_api_key": SecretStr("test-key"),
            "openai_compatible_model": "test-model",
            "openai_compatible_timeout_seconds": 30.0,
        }
        base.update(missing_kwargs)
        config = make_settings(**base)
        with pytest.raises(ProviderConfigurationError):
            get_project_maintenance_composition(configured_settings=config)
