"""OpenAI-compatible structured output provider for chapter reviews."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.agents.chapter_review_contracts import (
    ChapterReviewReport,
    ChapterReviewRequest,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewerRole,
)
from app.agents.context_assembly import (
    AssembledReviewContext,
    assemble_review_context,
)
from app.agents.profiles import AgentProfile
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputProvenance,
    StructuredOutputRequest,
)
from app.llm.openai_compatible_provider import OpenAICompatibleStructuredOutputTransport

if TYPE_CHECKING:
    pass


class OpenAICompatibleChapterReviewProvider:
    """Adapts chapter review protocols to StructuredOutputGateway via OpenAI-compatible transport."""

    __slots__ = (
        "_api_key",
        "_base_url",
        "_client",
        "_gateways",
        "_model",
        "_timeout_seconds",
        "_transport",
        "last_input_tokens",
        "last_output_tokens",
        "last_provenance",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._transport = OpenAICompatibleStructuredOutputTransport(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self._gateways: dict[tuple[str, str | None, str], StructuredOutputGateway[ChapterReviewReport]] = {}
        self.last_provenance: StructuredOutputProvenance | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _get_gateway(self, profile: AgentProfile) -> StructuredOutputGateway[ChapterReviewReport]:
        key = (profile.name, profile.mode, profile.version)
        if key not in self._gateways:
            model_identifier = self._model if self._model is not None else profile.model.model
            gateway_profile = StructuredOutputProfile(
                profile_id=f"{profile.name}_{profile.mode or 'default'}_{profile.version}",
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=profile.system_prompt,
                output_schema_name="chapter_review_report",
                output_schema=ChapterReviewReport,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=1_000_000,
                max_output_bytes=2_000_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    @staticmethod
    def _build_user_prompt(
        request: ChapterReviewRequest,
        assembled: AssembledReviewContext,
        role: ReviewerRole,
        mode: str,
    ) -> str:
        lines: list[str] = [
            "--- Lineage and Review Scope ---",
            f"project_id: {request.project_id}",
            f"chapter_id: {request.chapter_id}",
            f"workflow_run_id: {request.workflow_run_id}",
            f"target_document_id: {request.target.document_id}",
            f"target_version_id: {request.target.version_id}",
            f"reviewer_role: {role.value}",
            f"review_mode: {mode}",
            "",
            "--- Approved Outline ---",
            assembled.outline_content,
            "",
            "--- Candidate Chapter Text Under Review ---",
        ]

        for seg in request.target.segments:
            lines.append(f"### Segment {seg.index}: {seg.title} (ID: {seg.segment_id})")
            lines.append(seg.content)

        if assembled.auxiliary_contexts:
            lines.append("")
            lines.append("--- Role-Specific Context Materials ---")
            for ctx in assembled.auxiliary_contexts:
                lines.append(f"[{ctx.kind.value}]:\n{ctx.content}")

        lines.append("")
        lines.append("--- Required Output Format ---")
        lines.append(
            "Produce a JSON object strictly conforming to chapter_review_report. "
            "Echo the exact lineage IDs and reviewer role/mode given above. "
            "Set passed=true if and only if there are NO blocking findings. "
            "If any blocking findings are present, passed MUST be false. "
            "Include summary, findings (sequence starting at 1, code, severity [blocking/warning/note], "
            "required [true if blocking, false otherwise], evidence_segment_ids matching segment UUIDs, "
            "rationale, suggested_action), and suggested_actions."
        )

        return "\n".join(lines)

    async def _invoke(
        self,
        request: ChapterReviewRequest,
        profile: AgentProfile,
        role: ReviewerRole,
        mode: str,
    ) -> ChapterReviewReport:
        assembled = assemble_review_context(request, profile, role)
        user_prompt = self._build_user_prompt(request, assembled, role, mode)
        gateway = self._get_gateway(profile)
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

    async def review_editor(
        self, request: EditorReviewRequest, profile: AgentProfile
    ) -> ChapterReviewReport:
        return await self._invoke(
            request, profile, role=ReviewerRole.EDITOR, mode="chapter_editor"
        )

    async def review_chief_final(
        self, request: ChiefEditorChapterFinalRequest, profile: AgentProfile
    ) -> ChapterReviewReport:
        return await self._invoke(
            request, profile, role=ReviewerRole.CHIEF_EDITOR, mode="chapter_chief_final"
        )

    async def review_lore_final(
        self, request: LoreChapterFinalRequest, profile: AgentProfile
    ) -> ChapterReviewReport:
        return await self._invoke(
            request, profile, role=ReviewerRole.LORE, mode="chapter_final_lore"
        )


__all__ = ["OpenAICompatibleChapterReviewProvider"]
