"""Capture provider for inspecting exact payloads and prompts reaching the model boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agents.chapter_writer_contracts import (
    ChapterWriterRequest,
    InitialDraftRequest,
    ReviewDrivenRevisionRequest,
    SegmentDraftRequest,
    UserFeedbackRevisionRequest,
)
from app.agents.chapter_review_contracts import (
    ChapterReviewRequest,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewerRole,
)
from app.agents.context_assembly import (
    AssembledReviewContext,
    AssembledWriterContext,
    ContextProvenance,
    assemble_review_context,
    assemble_writer_context,
)
from app.agents.profiles import AgentProfile


@dataclass(frozen=True)
class CapturedInvocation:
    """Detailed record of what reached the provider/LLM model boundary."""

    role: str
    profile_name: str
    profile_mode: str | None
    profile_version: str
    system_prompt: str
    outline_content: str
    context_kinds: tuple[str, ...]
    source_versions: dict[str, str]
    total_tokens_estimate: int
    provenance: ContextProvenance


class CaptureChapterWriterProvider:
    """Captures exact writer invocations while returning valid candidate chapter outputs."""

    def __init__(self) -> None:
        self.invocations: list[CapturedInvocation] = []

    def _capture_and_generate(
        self,
        request: ChapterWriterRequest,
        profile: AgentProfile,
        expected_mode: str,
    ) -> dict[str, object]:
        assembled: AssembledWriterContext = assemble_writer_context(request, profile)

        source_versions: dict[str, str] = {
            "approved_outline_version_id": str(request.approved_outline.version_id),
        }
        source_draft = getattr(request, "source_draft", None)
        if source_draft is not None:
            source_versions["source_draft_version_id"] = str(source_draft.version_id)

        self.invocations.append(
            CapturedInvocation(
                role="writer",
                profile_name=profile.name,
                profile_mode=profile.mode,
                profile_version=profile.version,
                system_prompt=assembled.system_prompt,
                outline_content=assembled.outline_content,
                context_kinds=tuple(c.kind.value for c in assembled.auxiliary_contexts),
                source_versions=source_versions,
                total_tokens_estimate=assembled.provenance.estimated_total_tokens,
                provenance=assembled.provenance,
            )
        )

        target_ids = getattr(request, "target_segment_ids", None)
        targets = set(target_ids) if target_ids is not None else {s.segment_id for s in request.allowed_segments}
        source_segments = {
            item.segment_id: item
            for item in getattr(getattr(request, "source_draft", None), "segments", ())
        }

        candidate_segments = [
            {
                "segment_id": str(segment.segment_id),
                "index": segment.index,
                "title": segment.title,
                "content": (
                    f"{source_segments[segment.segment_id].content}\n\n"
                    f"Revised segment {segment.index} incorporating craft guidance."
                    if segment.segment_id in source_segments
                    else (
                        f"{segment.title}\n\n"
                        f"Draft for segment {segment.index}: {segment.brief}"
                    )
                ),
            }
            for segment in request.allowed_segments
            if segment.segment_id in targets
        ]

        source_version = getattr(getattr(request, "source_draft", None), "version_id", None)
        return {
            "project_id": str(request.project_id),
            "chapter_id": str(request.chapter_id),
            "workflow_run_id": str(request.workflow_run_id),
            "approved_outline_document_id": str(request.approved_outline.document_id),
            "approved_outline_version_id": str(request.approved_outline.version_id),
            "source_draft_document_id": (
                str(request.source_draft.document_id)
                if isinstance(request, (UserFeedbackRevisionRequest, ReviewDrivenRevisionRequest))
                else None
            ),
            "source_draft_version_id": str(source_version) if source_version is not None else None,
            "complete_chapter": isinstance(request, InitialDraftRequest),
            "segments": candidate_segments,
            "summary": "High-quality candidate generated at model boundary.",
            "self_check": {
                "outline_followed": True,
                "allowed_segments_only": True,
                "continuity_checked": True,
                "notes": ["Context and quality prompt applied at boundary."],
            },
            "uncertainty_markers": [],
        }

    async def draft_initial(
        self, request: InitialDraftRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_generate(request, profile, "initial_draft")

    async def draft_segments(
        self, request: SegmentDraftRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_generate(request, profile, "segment_draft")

    async def revise_from_user_feedback(
        self, request: UserFeedbackRevisionRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_generate(request, profile, "user_feedback_revision")

    async def revise_from_review(
        self, request: ReviewDrivenRevisionRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_generate(request, profile, "review_driven_revision")


class CaptureChapterReviewProvider:
    """Captures exact reviewer invocations while returning valid review reports."""

    def __init__(
        self,
        *,
        forced_outcome: Literal["passed", "warning", "blocking"] = "passed",
        findings: list[dict[str, object]] | None = None,
    ) -> None:
        self.forced_outcome = forced_outcome
        self.custom_findings = findings or []
        self.invocations: list[CapturedInvocation] = []

    def _capture_and_report(
        self,
        request: ChapterReviewRequest,
        profile: AgentProfile,
        role: ReviewerRole,
        mode: str,
    ) -> dict[str, object]:
        assembled: AssembledReviewContext = assemble_review_context(request, profile, role)

        source_versions: dict[str, str] = {
            "approved_outline_version_id": str(request.approved_outline.version_id),
            "target_version_id": str(request.target.version_id),
        }
        self.invocations.append(
            CapturedInvocation(
                role=role.value,
                profile_name=profile.name,
                profile_mode=profile.mode,
                profile_version=profile.version,
                system_prompt=assembled.system_prompt,
                outline_content=assembled.outline_content,
                context_kinds=tuple(c.kind.value for c in assembled.auxiliary_contexts),
                source_versions=source_versions,
                total_tokens_estimate=assembled.provenance.estimated_total_tokens,
                provenance=assembled.provenance,
            )
        )

        findings: list[dict[str, object]] = []
        suggested_actions: list[str] = []

        if self.custom_findings:
            findings.extend(self.custom_findings)
            for f in findings:
                action = str(f.get("suggested_action", ""))
                if action and action not in suggested_actions:
                    suggested_actions.append(action)
        elif self.forced_outcome != "passed":
            is_blocking = self.forced_outcome == "blocking"
            severity = "blocking" if is_blocking else "warning"
            action = "Revise chapter candidate according to editorial guidelines."
            findings.append(
                {
                    "sequence": 1,
                    "code": f"capture_{severity}",
                    "severity": severity,
                    "required": is_blocking,
                    "evidence_segment_ids": [str(request.target.segments[0].segment_id)],
                    "rationale": f"Captured {severity} rationale at model boundary.",
                    "suggested_action": action,
                }
            )
            suggested_actions.append(action)

        has_blocking = any(f.get("severity") == "blocking" for f in findings)
        passed = not has_blocking

        return {
            "project_id": str(request.project_id),
            "chapter_id": str(request.chapter_id),
            "workflow_run_id": str(request.workflow_run_id),
            "reviewer_role": role.value,
            "review_mode": mode,
            "target_document_id": str(request.target.document_id),
            "target_version_id": str(request.target.version_id),
            "passed": passed,
            "summary": f"Review evaluation completed with outcome: {'PASS' if passed else 'FAIL'}.",
            "findings": findings,
            "suggested_actions": suggested_actions,
        }

    async def review_editor(
        self, request: EditorReviewRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_report(
            request, profile, ReviewerRole.EDITOR, "chapter_editor"
        )

    async def review_chief_final(
        self, request: ChiefEditorChapterFinalRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_report(
            request, profile, ReviewerRole.CHIEF_EDITOR, "chapter_chief_final"
        )

    async def review_lore_final(
        self, request: LoreChapterFinalRequest, profile: AgentProfile
    ) -> object:
        return self._capture_and_report(
            request, profile, ReviewerRole.LORE, "chapter_final_lore"
        )


__all__ = [
    "CaptureChapterReviewProvider",
    "CaptureChapterWriterProvider",
    "CapturedInvocation",
]
