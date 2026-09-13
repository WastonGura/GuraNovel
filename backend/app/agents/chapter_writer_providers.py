"""OpenAI-compatible structured output provider for chapter candidate generation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from app.agents.chapter_writer_contracts import (
    CandidateChapterOutput,
    ChapterWriterRequest,
    InitialDraftRequest,
    ReviewDrivenRevisionRequest,
    SegmentDraftRequest,
    UserFeedbackRevisionRequest,
)
from app.agents.context_assembly import (
    AssembledWriterContext,
    assemble_writer_context,
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


class OpenAICompatibleChapterWriterProvider:
    """Adapts chapter writer protocols to StructuredOutputGateway via OpenAI-compatible transport."""

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
        self._gateways: dict[tuple[str, str | None, str], StructuredOutputGateway[CandidateChapterOutput]] = {}
        self.last_provenance: StructuredOutputProvenance | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _get_gateway(self, profile: AgentProfile) -> StructuredOutputGateway[CandidateChapterOutput]:
        key = (profile.name, profile.mode, profile.version)
        if key not in self._gateways:
            model_identifier = self._model if self._model is not None else profile.model.model
            gateway_profile = StructuredOutputProfile(
                profile_id=f"{profile.name}_{profile.mode or 'default'}_{profile.version}",
                provider_kind="openai_compatible",
                model_identifier=model_identifier,
                prompt_template_version=profile.version,
                system_prompt=profile.system_prompt,
                output_schema_name="candidate_chapter_output",
                output_schema=CandidateChapterOutput,
                timeout_seconds=self._timeout_seconds,
                max_input_chars=1_000_000,
                max_output_bytes=4_000_000,
                temperature=profile.model.temperature,
                top_p=profile.model.top_p,
                max_tokens=profile.model.max_tokens,
            )
            self._gateways[key] = StructuredOutputGateway(gateway_profile, self._transport)
        return self._gateways[key]

    @staticmethod
    def _build_user_prompt(
        request: ChapterWriterRequest,
        assembled: AssembledWriterContext,
    ) -> str:
        source_draft = getattr(request, "source_draft", None)
        source_doc_id: UUID | None = getattr(source_draft, "document_id", None)
        source_ver_id: UUID | None = getattr(source_draft, "version_id", None)
        is_complete = isinstance(request, InitialDraftRequest)

        lines: list[str] = [
            "--- Lineage and Identity ---",
            f"project_id: {request.project_id}",
            f"chapter_id: {request.chapter_id}",
            f"workflow_run_id: {request.workflow_run_id}",
            f"approved_outline_document_id: {request.approved_outline.document_id}",
            f"approved_outline_version_id: {request.approved_outline.version_id}",
            f"source_draft_document_id: {source_doc_id if source_doc_id is not None else 'null'}",
            f"source_draft_version_id: {source_ver_id if source_ver_id is not None else 'null'}",
            f"complete_chapter: {'true' if is_complete else 'false'}",
            "",
            "--- Approved Outline ---",
            assembled.outline_content,
            "",
            "--- Allowed Chapter Segments ---",
        ]

        target_ids = set(getattr(request, "target_segment_ids", (s.segment_id for s in request.allowed_segments)))
        for seg in assembled.active_segments:
            marker = "[TARGET TO GENERATE/REVISE]" if seg.segment_id in target_ids else "[CONTEXT ONLY]"
            lines.append(
                f"- Segment {seg.index} {marker}: ID={seg.segment_id} | Title={seg.title} | Brief={seg.brief}"
            )

        if assembled.source_segments:
            lines.append("")
            lines.append("--- Source Draft Content ---")
            for s_seg in assembled.source_segments:
                lines.append(f"### Segment {s_seg.index} (ID: {s_seg.segment_id}, Title: {s_seg.title})")
                lines.append(s_seg.content)

        if isinstance(request, UserFeedbackRevisionRequest) and request.feedback_refs:
            lines.append("")
            lines.append("--- User Feedback Instructions ---")
            for ref in request.feedback_refs:
                lines.append(f"- Feedback ({ref.feedback_id}): {ref.instruction}")

        if isinstance(request, ReviewDrivenRevisionRequest) and request.selected_findings:
            lines.append("")
            lines.append("--- Review Findings to Address ---")
            for item in request.selected_findings:
                ev = ", ".join(str(s_id) for s_id in item.finding.evidence_segment_ids)
                lines.append(
                    f"- [{item.finding.severity.value.upper()}] Code: {item.finding.code} | Evidences: [{ev}]"
                )
                lines.append(f"  Rationale: {item.finding.rationale}")
                lines.append(f"  Suggested Action: {item.finding.suggested_action}")

        if assembled.auxiliary_contexts:
            lines.append("")
            lines.append("--- Auxiliary Context Materials ---")
            for ctx in assembled.auxiliary_contexts:
                lines.append(f"[{ctx.kind.value}] (Doc: {ctx.document_id}):\n{ctx.content}")

        lines.append("")
        lines.append("--- Required Output Format ---")
        lines.append(
            "Produce a JSON object strictly conforming to candidate_chapter_output. "
            "Echo the exact lineage IDs given above. Generate segments with matching segment_id, "
            "index, title, and prose content. Include summary, self_check, and uncertainty_markers."
        )

        return "\n".join(lines)

    async def _invoke(
        self,
        request: ChapterWriterRequest,
        profile: AgentProfile,
    ) -> CandidateChapterOutput:
        assembled = assemble_writer_context(request, profile)
        user_prompt = self._build_user_prompt(request, assembled)
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

    async def draft_initial(
        self, request: InitialDraftRequest, profile: AgentProfile
    ) -> CandidateChapterOutput:
        return await self._invoke(request, profile)

    async def draft_segments(
        self, request: SegmentDraftRequest, profile: AgentProfile
    ) -> CandidateChapterOutput:
        return await self._invoke(request, profile)

    async def revise_from_user_feedback(
        self, request: UserFeedbackRevisionRequest, profile: AgentProfile
    ) -> CandidateChapterOutput:
        return await self._invoke(request, profile)

    async def revise_from_review(
        self, request: ReviewDrivenRevisionRequest, profile: AgentProfile
    ) -> CandidateChapterOutput:
        return await self._invoke(request, profile)


__all__ = ["OpenAICompatibleChapterWriterProvider"]
