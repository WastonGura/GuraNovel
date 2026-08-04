"""Deterministic, credential-free chapter candidate provider for tests."""

from __future__ import annotations

import json

from pydantic import BaseModel

from app.agents.chapter_writer_contracts import (
    ChapterWriterRequest,
    InitialDraftRequest,
    ReviewDrivenRevisionRequest,
    SegmentDraftRequest,
    UserFeedbackRevisionRequest,
)
from app.agents.profiles import AgentProfile


def canonical_chapter_json_bytes(result: BaseModel) -> bytes:
    """Encode a validated candidate identically across calls and processes."""

    return json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DeterministicChapterWriterProvider:
    """Produce fixed candidates solely from typed request data and bundled profiles."""

    @staticmethod
    def _candidate(
        request: ChapterWriterRequest,
        profile: AgentProfile | None,
        *,
        expected_name: str,
        expected_mode: str,
    ) -> dict[str, object]:
        if profile is not None and (profile.name != expected_name or profile.mode != expected_mode):
            raise ValueError("unexpected chapter writer profile")
        targets = set(request.target_segment_ids)
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
                    f"Deterministic revision for segment {segment.index}."
                    if segment.segment_id in source_segments
                    else (
                        f"{segment.title}\n\n"
                        f"Deterministic candidate for segment {segment.index}: {segment.brief}"
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
            "summary": "Generated a deterministic, non-canonical chapter candidate.",
            "self_check": {
                "outline_followed": True,
                "allowed_segments_only": True,
                "continuity_checked": True,
                "notes": ["Candidate identity and version bindings were preserved."],
            },
            "uncertainty_markers": [],
        }

    def initial_draft_sync(self, request: InitialDraftRequest) -> dict[str, object]:
        """Synchronous helper for contract mutation tests; performs no I/O."""

        return self._candidate(
            request,
            None,
            expected_name="writer_agent",
            expected_mode="initial_draft",
        )

    async def draft_initial(self, request: InitialDraftRequest, profile: AgentProfile) -> object:
        return self._candidate(
            request, profile, expected_name="writer_agent", expected_mode="initial_draft"
        )

    async def draft_segments(self, request: SegmentDraftRequest, profile: AgentProfile) -> object:
        return self._candidate(
            request, profile, expected_name="writer_agent", expected_mode="segment_draft"
        )

    async def revise_from_user_feedback(
        self, request: UserFeedbackRevisionRequest, profile: AgentProfile
    ) -> object:
        return self._candidate(
            request,
            profile,
            expected_name="revision_agent",
            expected_mode="user_feedback_revision",
        )

    async def revise_from_review(
        self, request: ReviewDrivenRevisionRequest, profile: AgentProfile
    ) -> object:
        return self._candidate(
            request,
            profile,
            expected_name="revision_agent",
            expected_mode="review_driven_revision",
        )


__all__ = ["DeterministicChapterWriterProvider", "canonical_chapter_json_bytes"]
