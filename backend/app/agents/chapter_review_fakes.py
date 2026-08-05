"""Deterministic, zero-I/O fake provider for chapter review contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal

from pydantic import ValidationError

from app.agents.chapter_review_contracts import (
    ChapterReviewReport,
    ChapterReviewRequest,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewerRole,
)
from app.agents.profiles import AgentProfile
from app.llm.errors import ProviderConfigurationError


ReviewFakeOutcome = Literal["passed", "warning", "blocking"]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_review_json_bytes(value: ChapterReviewReport) -> bytes:
    validated: ChapterReviewReport | None = None
    try:
        if type(value) is not ChapterReviewReport:
            raise TypeError("invalid review report envelope")
        validated = ChapterReviewReport.model_validate(
            value.model_dump(mode="json", warnings="none")
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        pass
    if validated is None:
        raise ProviderConfigurationError() from None
    return _canonical_bytes(validated.model_dump(mode="json"))


class DeterministicChapterReviewProvider:
    """Configurable passed/warning/blocking fake with no external authority."""

    def __init__(self, *, outcome: ReviewFakeOutcome = "passed") -> None:
        if outcome not in ("passed", "warning", "blocking"):
            raise ProviderConfigurationError() from None
        self._outcome = outcome

    @staticmethod
    def _valid_profile(profile: AgentProfile, *, name: str, mode: str | None, role: str) -> None:
        if (
            profile.name != name
            or profile.mode != mode
            or profile.agent_role != role
            or profile.output_schema != "chapter_review_report"
        ):
            raise ProviderConfigurationError() from None

    def _report(
        self,
        request: ChapterReviewRequest,
        *,
        expected_request_type: (
            type[EditorReviewRequest]
            | type[ChiefEditorChapterFinalRequest]
            | type[LoreChapterFinalRequest]
        ),
        reviewer_role: ReviewerRole,
        mode: str,
    ) -> dict[str, object]:
        request_type = type(request)
        validated_request: ChapterReviewRequest | None = None
        try:
            if request_type is not expected_request_type:
                raise TypeError("invalid chapter review request envelope")
            validated_request = request_type.model_validate(
                request.model_dump(mode="json", warnings="none")
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            pass
        if validated_request is None:
            raise ProviderConfigurationError() from None
        request = validated_request
        input_fingerprint = sha256(_canonical_bytes(request.model_dump(mode="json"))).hexdigest()[
            :16
        ]
        findings: list[dict[str, object]] = []
        suggested_actions: list[str] = []
        if self._outcome != "passed":
            blocking = self._outcome == "blocking"
            severity = "blocking" if blocking else "warning"
            action = (
                "Ask the writer to address the identified issue in a new candidate."
                if blocking
                else "Consider the finding before accepting the candidate."
            )
            findings.append(
                {
                    "sequence": 1,
                    "code": f"deterministic_{severity}",
                    "severity": severity,
                    "required": blocking,
                    "evidence_segment_ids": [str(request.target.segments[0].segment_id)],
                    "rationale": f"Deterministic {severity} outcome for contract testing.",
                    "suggested_action": action,
                }
            )
            suggested_actions.append(action)
        return {
            "project_id": str(request.project_id),
            "chapter_id": str(request.chapter_id),
            "workflow_run_id": str(request.workflow_run_id),
            "reviewer_role": reviewer_role.value,
            "review_mode": mode,
            "target_document_id": str(request.target.document_id),
            "target_version_id": str(request.target.version_id),
            "passed": self._outcome != "blocking",
            "summary": (
                f"Deterministic {self._outcome} chapter review for input {input_fingerprint}."
            ),
            "findings": findings,
            "suggested_actions": suggested_actions,
        }

    async def review_editor(self, request: EditorReviewRequest, profile: AgentProfile) -> object:
        self._valid_profile(
            profile,
            name="editor_agent",
            mode=None,
            role="editor_agent",
        )
        return self.editor_sync(request)

    def editor_sync(self, request: EditorReviewRequest) -> dict[str, object]:
        return self._report(
            request,
            expected_request_type=EditorReviewRequest,
            reviewer_role=ReviewerRole.EDITOR,
            mode="chapter_editor",
        )

    async def review_chief_final(
        self, request: ChiefEditorChapterFinalRequest, profile: AgentProfile
    ) -> object:
        self._valid_profile(
            profile,
            name="chief_editor",
            mode="chapter_final",
            role="chief_editor_agent",
        )
        return self._report(
            request,
            expected_request_type=ChiefEditorChapterFinalRequest,
            reviewer_role=ReviewerRole.CHIEF_EDITOR,
            mode="chapter_chief_final",
        )

    async def review_lore_final(
        self, request: LoreChapterFinalRequest, profile: AgentProfile
    ) -> object:
        self._valid_profile(
            profile,
            name="lore_agent",
            mode="chapter_final",
            role="lore_agent",
        )
        return self._report(
            request,
            expected_request_type=LoreChapterFinalRequest,
            reviewer_role=ReviewerRole.LORE,
            mode="chapter_final_lore",
        )


__all__ = [
    "DeterministicChapterReviewProvider",
    "ReviewFakeOutcome",
    "canonical_review_json_bytes",
]
