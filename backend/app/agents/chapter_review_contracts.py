"""Strict, provider-neutral chapter review contracts.

Review requests carry bounded snapshots only. Reports are advisory and are rebound to
the server-owned request before they can leave the provider boundary. These contracts
prove structural consistency, not database existence or currentness; a future
orchestrator must verify both immediately before invocation and persistence.
"""

from __future__ import annotations

from enum import StrEnum
import json
import re
from typing import ClassVar, Self
import unicodedata
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.llm.errors import ProviderInvalidOutputError


MAX_CHAPTER_REVIEW_REQUEST_BYTES = 262_144
MAX_CHAPTER_REVIEW_REPORT_BYTES = 65_536
_STABLE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_UUID_FIELDS = (
    "project_id",
    "chapter_id",
    "workflow_run_id",
    "document_id",
    "version_id",
    "segment_id",
    "target_document_id",
    "target_version_id",
)


def _canonical_uuid(value: object) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid reference") from None
    if parsed.int == 0 or str(parsed) != str(value).lower():
        raise ValueError("invalid reference")
    return parsed


def _bounded_text(value: str, label: str, *, max_bytes: int) -> str:
    if (
        value != value.strip()
        or not value
        or any(
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
            for character in value
        )
    ):
        raise ValueError(f"invalid {label}")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"invalid {label}") from None
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} is too large")
    return value


def _json_size(value: BaseModel) -> int:
    return len(
        json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


class _StrictReviewModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator(*_UUID_FIELDS, mode="before", check_fields=False)
    @classmethod
    def canonical_non_nil_uuid(cls, value: object) -> UUID | None:
        if value is None:
            return None
        return _canonical_uuid(value)

    @field_validator(
        "segments",
        "contexts",
        "findings",
        "evidence_segment_ids",
        "suggested_actions",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def immutable_collections(cls, value: object) -> object:
        if type(value) is list:
            return tuple(value)
        return value


class ReviewContextKind(StrEnum):
    STYLE_GUIDE = "style_guide"
    PREVIOUS_CHAPTER_SUMMARY = "previous_chapter_summary"
    AUDIENCE_GOAL = "audience_goal"
    LORE_BOUNDARY = "lore_boundary"
    CHARACTER_STATE = "character_state"
    TIMELINE = "timeline"
    FORESHADOWING = "foreshadowing"


class ReviewerRole(StrEnum):
    EDITOR = "editor_agent"
    CHIEF_EDITOR = "chief_editor_agent"
    LORE = "lore_agent"


class ReviewFindingSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    NOTE = "note"


class ReviewSegmentSnapshot(_StrictReviewModel):
    segment_id: UUID
    index: int = Field(ge=1, le=1024)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=65_536, repr=False)

    @field_validator("title", "content")
    @classmethod
    def valid_text(cls, value: str, info: object) -> str:
        field = getattr(info, "field_name", "text")
        limit = 512 if field == "title" else 65_536
        return _bounded_text(value, field, max_bytes=limit)


class ChapterReviewTarget(_StrictReviewModel):
    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
    segments: tuple[ReviewSegmentSnapshot, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def canonical_segments(self) -> Self:
        ids = [item.segment_id for item in self.segments]
        indexes = [item.index for item in self.segments]
        if len(ids) != len(set(ids)) or indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("invalid target segment references")
        return self


class ApprovedOutlineSnapshot(_StrictReviewModel):
    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
    content: str = Field(min_length=1, max_length=32_768, repr=False)

    @field_validator("content")
    @classmethod
    def valid_content(cls, value: str) -> str:
        return _bounded_text(value, "outline content", max_bytes=32_768)


class ReviewContextSnapshot(_StrictReviewModel):
    project_id: UUID
    document_id: UUID
    version_id: UUID
    kind: ReviewContextKind
    content: str = Field(min_length=1, max_length=32_768, repr=False)

    @field_validator("kind", mode="before")
    @classmethod
    def typed_kind(cls, value: object) -> ReviewContextKind:
        if isinstance(value, ReviewContextKind):
            return value
        if type(value) is str:
            try:
                return ReviewContextKind(value)
            except ValueError:
                pass
        raise ValueError("invalid review context kind")

    @field_validator("content")
    @classmethod
    def valid_content(cls, value: str) -> str:
        return _bounded_text(value, "review context", max_bytes=32_768)


class _ChapterReviewRequest(_StrictReviewModel):
    _ALLOWED_CONTEXTS: ClassVar[frozenset[ReviewContextKind]]

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    target: ChapterReviewTarget
    approved_outline: ApprovedOutlineSnapshot
    contexts: tuple[ReviewContextSnapshot, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def exact_bound_context(self) -> Self:
        chapter_lineage = (self.project_id, self.chapter_id)
        if (
            (self.target.project_id, self.target.chapter_id) != chapter_lineage
            or (self.approved_outline.project_id, self.approved_outline.chapter_id)
            != chapter_lineage
            or any(item.project_id != self.project_id for item in self.contexts)
        ):
            raise ValueError("cross-project review context")
        context_documents = [item.document_id for item in self.contexts]
        if len(context_documents) != len(set(context_documents)):
            raise ValueError("duplicate review context reference")
        if any(item.kind not in self._ALLOWED_CONTEXTS for item in self.contexts):
            raise ValueError("review context is not allowed for this reviewer")
        if _json_size(self) > MAX_CHAPTER_REVIEW_REQUEST_BYTES:
            raise ValueError("chapter review request is too large")
        return self


class EditorReviewRequest(_ChapterReviewRequest):
    _ALLOWED_CONTEXTS = frozenset(
        {
            ReviewContextKind.STYLE_GUIDE,
            ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY,
        }
    )


class ChiefEditorChapterFinalRequest(_ChapterReviewRequest):
    _ALLOWED_CONTEXTS = frozenset(
        {
            ReviewContextKind.STYLE_GUIDE,
            ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY,
            ReviewContextKind.AUDIENCE_GOAL,
        }
    )


class LoreChapterFinalRequest(_ChapterReviewRequest):
    _ALLOWED_CONTEXTS = frozenset(
        {
            ReviewContextKind.LORE_BOUNDARY,
            ReviewContextKind.CHARACTER_STATE,
            ReviewContextKind.TIMELINE,
            ReviewContextKind.FORESHADOWING,
        }
    )


ChapterReviewRequest = (
    EditorReviewRequest | ChiefEditorChapterFinalRequest | LoreChapterFinalRequest
)


class ChapterReviewFinding(_StrictReviewModel):
    sequence: int = Field(ge=1, le=128)
    code: str = Field(min_length=1, max_length=64)
    severity: ReviewFindingSeverity
    required: bool
    evidence_segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=16)
    rationale: str = Field(min_length=1, max_length=4000, repr=False)
    suggested_action: str = Field(min_length=1, max_length=2000, repr=False)

    @field_validator("evidence_segment_ids", mode="before")
    @classmethod
    def canonical_evidence_ids(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            return value
        return tuple(_canonical_uuid(item) for item in value)

    @field_validator("code")
    @classmethod
    def stable_code(cls, value: str) -> str:
        if _STABLE_CODE.fullmatch(value) is None:
            raise ValueError("invalid finding code")
        return value

    @field_validator("severity", mode="before")
    @classmethod
    def typed_severity(cls, value: object) -> ReviewFindingSeverity:
        if isinstance(value, ReviewFindingSeverity):
            return value
        if type(value) is str:
            try:
                return ReviewFindingSeverity(value)
            except ValueError:
                pass
        raise ValueError("invalid review finding severity")

    @field_validator("rationale", "suggested_action")
    @classmethod
    def valid_text(cls, value: str, info: object) -> str:
        field = getattr(info, "field_name", "finding text")
        limit = 4000 if field == "rationale" else 2000
        return _bounded_text(value, field, max_bytes=limit)

    @model_validator(mode="after")
    def consistent_required_state(self) -> Self:
        if self.required is not (self.severity is ReviewFindingSeverity.BLOCKING):
            raise ValueError("finding required state contradicts severity")
        if len(self.evidence_segment_ids) != len(set(self.evidence_segment_ids)):
            raise ValueError("duplicate evidence segment reference")
        return self


class ChapterReviewReport(_StrictReviewModel):
    """Advisory report. No text, persistence, approval, or routing authority exists."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    reviewer_role: ReviewerRole
    review_mode: str
    target_document_id: UUID
    target_version_id: UUID
    passed: bool
    summary: str = Field(min_length=1, max_length=4000, repr=False)
    findings: tuple[ChapterReviewFinding, ...] = Field(default=(), max_length=128)
    suggested_actions: tuple[str, ...] = Field(default=(), max_length=16, repr=False)

    @field_validator("reviewer_role", mode="before")
    @classmethod
    def typed_role(cls, value: object) -> ReviewerRole:
        if isinstance(value, ReviewerRole):
            return value
        if type(value) is str:
            try:
                return ReviewerRole(value)
            except ValueError:
                pass
        raise ValueError("invalid reviewer role")

    @field_validator("review_mode")
    @classmethod
    def exact_mode(cls, value: str) -> str:
        if value not in (
            "chapter_editor",
            "chapter_chief_final",
            "chapter_final_lore",
        ):
            raise ValueError("invalid review mode")
        return value

    @field_validator("summary")
    @classmethod
    def valid_summary(cls, value: str) -> str:
        return _bounded_text(value, "review summary", max_bytes=4000)

    @field_validator("suggested_actions")
    @classmethod
    def valid_suggested_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_bounded_text(item, "suggested action", max_bytes=2000) != item for item in value):
            raise ValueError("invalid suggested action")
        if len(value) != len(set(value)):
            raise ValueError("duplicate suggested action")
        return value

    @model_validator(mode="after")
    def consistent_report(self) -> Self:
        sequences = [item.sequence for item in self.findings]
        codes = [item.code for item in self.findings]
        if sequences != list(range(1, len(sequences) + 1)) or len(codes) != len(set(codes)):
            raise ValueError("duplicate or unordered review finding")
        has_blocking = any(
            item.severity is ReviewFindingSeverity.BLOCKING for item in self.findings
        )
        if self.passed is has_blocking:
            raise ValueError("review outcome contradicts blocking findings")
        if _json_size(self) > MAX_CHAPTER_REVIEW_REPORT_BYTES:
            raise ValueError("chapter review report is too large")
        return self


def validate_chapter_review_report(
    raw_output: object,
    *,
    request: ChapterReviewRequest,
    reviewer_role: ReviewerRole,
    mode: str,
) -> ChapterReviewReport:
    request_type = type(request)
    request_bindings: dict[type[object], tuple[ReviewerRole, str]] = {
        EditorReviewRequest: (ReviewerRole.EDITOR, "chapter_editor"),
        ChiefEditorChapterFinalRequest: (
            ReviewerRole.CHIEF_EDITOR,
            "chapter_chief_final",
        ),
        LoreChapterFinalRequest: (ReviewerRole.LORE, "chapter_final_lore"),
    }
    expected_binding = request_bindings.get(request_type)
    validated_request: ChapterReviewRequest | None = None
    try:
        if (
            type(reviewer_role) is not ReviewerRole
            or type(mode) is not str
            or expected_binding is None
            or expected_binding != (reviewer_role, mode)
        ):
            raise TypeError("invalid review authority")
        validated_request = request_type.model_validate(
            request.model_dump(mode="json", warnings="none")
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        pass
    if validated_request is None:
        raise ProviderInvalidOutputError() from None

    result: ChapterReviewReport | None = None
    try:
        if isinstance(raw_output, BaseModel):
            if type(raw_output) is not ChapterReviewReport:
                raise TypeError("provider output must use the review report envelope")
            raw_output = raw_output.model_dump(mode="json")
        result = ChapterReviewReport.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        pass
    if result is None:
        raise ProviderInvalidOutputError() from None

    if (
        result.project_id,
        result.chapter_id,
        result.workflow_run_id,
        result.target_document_id,
        result.target_version_id,
        result.reviewer_role,
        result.review_mode,
    ) != (
        validated_request.project_id,
        validated_request.chapter_id,
        validated_request.workflow_run_id,
        validated_request.target.document_id,
        validated_request.target.version_id,
        reviewer_role,
        mode,
    ):
        raise ProviderInvalidOutputError() from None
    known_segments = {item.segment_id for item in validated_request.target.segments}
    if any(
        not set(finding.evidence_segment_ids) <= known_segments
        or finding.evidence_segment_ids
        != tuple(
            segment.segment_id
            for segment in validated_request.target.segments
            if segment.segment_id in finding.evidence_segment_ids
        )
        for finding in result.findings
    ):
        raise ProviderInvalidOutputError() from None
    return result


__all__ = [
    "ApprovedOutlineSnapshot",
    "ChapterReviewFinding",
    "ChapterReviewReport",
    "ChapterReviewRequest",
    "ChapterReviewTarget",
    "ChiefEditorChapterFinalRequest",
    "EditorReviewRequest",
    "LoreChapterFinalRequest",
    "ReviewContextKind",
    "ReviewContextSnapshot",
    "ReviewFindingSeverity",
    "ReviewerRole",
    "ReviewSegmentSnapshot",
    "validate_chapter_review_report",
]
