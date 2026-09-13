"""OpenAI-compatible providers for project maintenance analysis, planning, and review."""

from __future__ import annotations

from typing import Any
import httpx
from pydantic import BaseModel, SecretStr

from app.agents.maintenance_agents import (
    ApplyChangeProvider,
    MaintenanceImpactProvider,
    PostChangeProvider,
    RevisionPlanProvider,
)
from app.agents.maintenance_contracts import (
    ApplyChangeOutput,
    ApplyChangeRequest,
    ChiefEditorMaintenanceImpactOutput,
    ConsistencyReviewOutput,
    LoreImpactOutput,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionOperationKind,
    RevisionPlanOutput,
    RevisionPlanRequest,
)
from app.agents.profiles import AgentProfile
from app.llm.errors import ProviderConfigurationError
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputRequest,
)
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport


_SCHEMA_BY_NAME: dict[str, type[BaseModel]] = {
    "lore_maintenance_impact_output": LoreImpactOutput,
    "chief_editor_maintenance_impact_output": ChiefEditorMaintenanceImpactOutput,
    "revision_plan_output": RevisionPlanOutput,
    "apply_change_output": ApplyChangeOutput,
    "consistency_review_output": ConsistencyReviewOutput,
}


class OpenAICompatibleMaintenanceProvider(
    MaintenanceImpactProvider,
    RevisionPlanProvider,
    ApplyChangeProvider,
    PostChangeProvider,
):
    """Real OpenAI-compatible provider implementing all project maintenance protocols."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | str,
        *,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._gateways: dict[str, StructuredOutputGateway[Any]] = {}
        self.last_provenance: str | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def _get_gateway(self, profile: AgentProfile) -> StructuredOutputGateway[Any]:
        key = f"{profile.name}_{profile.mode or 'default'}_{profile.version}"
        if key not in self._gateways:
            schema_cls = _SCHEMA_BY_NAME.get(profile.output_schema)
            if schema_cls is None:
                raise ProviderConfigurationError()
            model_identifier = self._model if self._model is not None else profile.model.model
            gateway_profile = StructuredOutputProfile(
                profile_id=f"{profile.name}_{profile.mode or 'default'}_{profile.version}",
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=profile.system_prompt,
                output_schema_name=profile.output_schema,
                output_schema=schema_cls,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=100_000,
                max_output_bytes=200_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    @staticmethod
    def _build_maintenance_impact_user_prompt(
        request: MaintenanceImpactRequest, profile: AgentProfile
    ) -> str:
        lines = [
            "--- Project Maintenance Impact Request ---",
            f"Project ID: {request.project_id}",
            f"Workflow Run ID: {request.workflow_run_id}",
            f"Change Request ID: {request.change_request_id}",
            f"Change Request: {request.change_request}",
            "",
            "--- Document References ---",
        ]
        if request.document_refs:
            for doc in request.document_refs:
                lines.append(
                    f"- Document ID: {doc.document_id}, Current Version ID: {doc.current_version_id}"
                )
        else:
            lines.append("None provided.")
        lines.extend([
            "",
            "--- Output Requirements ---",
            "Analyze the structural, lore, and editorial impact of the proposed change.",
            "Return a JSON object conforming strictly to the declared output schema.",
            "Rules:",
            "1. affected_items: Must include at least 1 affected item.",
            "   Each item's stable_reference must match regex '^(chapter|character|world|outline|foreshadowing|timeline|style)/[a-z0-9][a-z0-9_-]{0,63}$' and begin with '{item_type}/'.",
            "2. If an affected item references a document, document_id and current_version_id must match one of the listed Document References.",
            "3. reason, impact_summary, instruction: Must be plain safe text without URL schemes, path traversal ('/', '\\', '..'), file paths, or credentials.",
            "4. safe_to_change: Set to true if there are no blocking warnings. Set to false if there is at least one blocking warning. (safe_to_change must be opposite of having blocking warnings).",
            "5. Every warning code must match regex '^[a-z][a-z0-9_]{0,63}$'. Warning affected_item_references must be a subset of the affected_items stable_references.",
        ])
        if profile.output_schema == "chief_editor_maintenance_impact_output":
            lines.extend([
                "6. reader_expectation_impact: One of 'low', 'medium', 'high', 'critical'.",
                "7. commercial_impact: One of 'low', 'medium', 'high', 'critical'.",
            ])
        return "\n".join(lines)

    @staticmethod
    def _build_revision_plan_user_prompt(
        request: RevisionPlanRequest, profile: AgentProfile
    ) -> str:
        lines = [
            "--- Project Revision Plan Request ---",
            f"Project ID: {request.project_id}",
            f"Workflow Run ID: {request.workflow_run_id}",
            f"Change Request ID: {request.change_request_id}",
            f"Change Request: {request.change_request}",
            "",
            "--- Target Documents ---",
        ]
        for doc in request.document_refs:
            lines.append(
                f"- Document ID: {doc.document_id}, Current Version ID: {doc.current_version_id}"
            )
        lines.extend([
            "",
            "--- Affected Items ---",
        ])
        for item in request.affected_items:
            doc_info = f" (doc: {item.document.document_id})" if item.document else ""
            lines.append(
                f"- Item ID: {item.affected_item_id}, Ref: {item.stable_reference}, "
                f"Type: {item.item_type.value}, Impact: {item.impact_level.value}{doc_info}: {item.reason}"
            )
        lines.extend([
            "",
            "--- Output Requirements ---",
            "Formulate an ordered sequence of revision operations to realize the requested change.",
            "Return a JSON object conforming strictly to revision_plan_output.",
            "Rules:",
            "1. plan_id: A valid non-nil UUID.",
            "2. operations: 1 to 128 operations. Sequence must be canonical 1-based index (1, 2, 3, ...).",
            "3. Each operation target must match one of the Target Documents (document_id, current_version_id).",
            "4. operation must be 'revise', 'retire', or 'retain'.",
            "5. affected_item_ids must only contain UUIDs from the listed Affected Items. If an affected item is tied to a document, that document must match the operation target.",
            "6. instruction and summary: Plain safe text without file paths, slashes, or backslashes.",
            "7. safety: Must have requires_user_confirmation: true, preserve_existing_versions: true, direct_write_authority: false.",
            "8. warnings: Optional list of warnings with valid UUIDs, valid codes, and affected_item_ids from the listed Affected Items.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _build_apply_change_user_prompt(
        request: ApplyChangeRequest, profile: AgentProfile
    ) -> str:
        lines = [
            "--- Apply Change Request (Archivist Proposal Only) ---",
            f"Project ID: {request.project_id}",
            f"Workflow Run ID: {request.workflow_run_id}",
            f"Change Request ID: {request.change_request_id}",
            f"Approval ID: {request.approval_id}",
            f"Revision Plan ID: {request.revision_plan_id}",
            f"Revision Plan Document ID: {request.revision_plan_document_id}",
            f"Revision Plan Version ID: {request.revision_plan_version_id}",
            "",
            "--- Approved Revision Operations ---",
        ]
        revise_ops = [
            op for op in request.operations if op.operation is RevisionOperationKind.REVISE
        ]
        for op in revise_ops:
            lines.append(
                f"- Operation ID: {op.operation_id}, Sequence: {op.sequence}, "
                f"Target Doc ID: {op.target.document_id}, Version ID: {op.target.current_version_id}, "
                f"Instruction: {op.instruction}"
            )
        lines.extend([
            "",
            "--- Output Requirements ---",
            "Generate candidate replacement contents for each approved 'revise' operation.",
            "You have NO direct write or database authority; you only propose version replacement bodies.",
            "Return a JSON object conforming strictly to apply_change_output.",
            "Rules:",
            "1. change_set_id: A valid non-nil UUID.",
            "2. The top-level lineage IDs (project_id, workflow_run_id, change_request_id, approval_id, "
            "revision_plan_id, revision_plan_document_id, revision_plan_version_id) MUST EXACTLY MATCH the request lineage above.",
            "3. proposed_edits: Exactly one edit per revise operation, with sequence starting from 1 (1, 2, ...).",
            "4. Each proposed edit must repeat the exact same lineage IDs as above.",
            "5. proposed_edit_id: A unique valid non-nil UUID.",
            "6. revision_operation_id: Must match the respective approved operation ID.",
            "7. document_id and expected_current_version_id: Must match the operation target document ID and current version ID.",
            "8. operation: Must be 'replace_content'.",
            "9. content: Proposed revised markdown/text content for the document (UTF-8, non-empty, safe characters).",
            "10. rationale: Plain safe text explaining the change.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _build_post_change_user_prompt(
        request: PostChangeRequest, profile: AgentProfile
    ) -> str:
        lines = [
            "--- Post-Change Consistency Review Request ---",
            f"Project ID: {request.project_id}",
            f"Workflow Run ID: {request.workflow_run_id}",
            f"Change Request ID: {request.change_request_id}",
            f"Approval ID: {request.approval_id}",
            f"Revision Plan ID: {request.revision_plan_id}",
            f"Revision Plan Document ID: {request.revision_plan_document_id}",
            f"Revision Plan Version ID: {request.revision_plan_version_id}",
            f"Change Set ID: {request.change_set_id}",
            "",
            "--- Applied Document Changes ---",
        ]
        for item in request.applied_changes:
            lines.append(
                f"- Doc ID: {item.document_id}, Previous Version: {item.previous_version_id}, "
                f"Current Version: {item.current_version_id}, Edit ID: {item.proposed_edit_id}"
            )
        lines.extend([
            "",
            "--- Output Requirements ---",
            "Perform post-change consistency review across the modified documents.",
            "Return a JSON object conforming strictly to consistency_review_output.",
            "Rules:",
            "1. review_id: A valid non-nil UUID.",
            "2. Top-level lineage IDs (project_id, workflow_run_id, change_request_id, approval_id, "
            "revision_plan_id, revision_plan_document_id, revision_plan_version_id, change_set_id) MUST EXACTLY MATCH the request above.",
            "3. outcome: 'clean', 'warning', or 'blocking'.",
            "4. If outcome is 'clean', findings must be empty [].",
            "5. If outcome is 'warning', findings must not be empty, all findings must have blocking=false and severity='warning'.",
            "6. If outcome is 'blocking', at least one finding must have blocking=true and severity='blocking'.",
            "7. findings sequence must be canonical 1, 2, 3...",
            "8. Each finding affected_documents must only reference documents from the Applied Document Changes above.",
            "9. suggested_corrective_action: Plain safe text without file paths or slashes.",
        ])
        return "\n".join(lines)

    async def analyze_maintenance_impact(
        self, request: MaintenanceImpactRequest, profile: AgentProfile
    ) -> object:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_maintenance_impact_user_prompt(request, profile)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def plan_revision(
        self, request: RevisionPlanRequest, profile: AgentProfile
    ) -> object:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_revision_plan_user_prompt(request, profile)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def propose_changes(
        self, request: ApplyChangeRequest, profile: AgentProfile
    ) -> object:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_apply_change_user_prompt(request, profile)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def review_consistency(
        self, request: PostChangeRequest, profile: AgentProfile
    ) -> object:
        gateway = self._get_gateway(profile)
        user_prompt = self._build_post_change_user_prompt(request, profile)
        response = await gateway.call(
            StructuredOutputRequest(
                profile_id=gateway.profile_id,
                user_prompt=user_prompt,
            )
        )
        self.last_provenance = response.provenance
        self.last_input_tokens = response.input_tokens
        self.last_output_tokens = response.output_tokens
        return response.result

    async def aclose(self) -> None:
        await self._transport.aclose()


# Explicit aliases matching specific protocol boundaries
OpenAICompatibleMaintenanceImpactProvider = OpenAICompatibleMaintenanceProvider
OpenAICompatibleRevisionPlanProvider = OpenAICompatibleMaintenanceProvider
OpenAICompatibleApplyChangeProvider = OpenAICompatibleMaintenanceProvider
OpenAICompatiblePostChangeProvider = OpenAICompatibleMaintenanceProvider


__all__ = [
    "OpenAICompatibleApplyChangeProvider",
    "OpenAICompatibleMaintenanceImpactProvider",
    "OpenAICompatibleMaintenanceProvider",
    "OpenAICompatiblePostChangeProvider",
    "OpenAICompatibleRevisionPlanProvider",
]
