"""Strict, provider-neutral contracts for chapter drafting and revision.

These models validate internal identity/version binding only.  A future orchestrator
must reload the referenced rows to prove existence and currentness before invocation.
"""

from __future__ import annotations

import json
from typing import Self
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


_UUID_FIELDS = (
    "project_id",
    "chapter_id",
    "workflow_run_id",
    "document_id",
    "version_id",
    "segment_id",
    "feedback_id",
    "report_id",
    "source_draft_version_id",
    "source_draft_document_id",
    "target_draft_version_id",
    "target_draft_document_id",
    "approved_outline_version_id",
    "approved_outline_document_id",
)

MAX_CANDIDATE_CONTENT_BYTES = 524_288
MAX_CANDIDATE_ENVELOPE_BYTES = 600_000
MAX_CHAPTER_REQUEST_ENVELOPE_BYTES = 32_768


def _canonical_uuid(value: object) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid reference") from None
    if parsed.int == 0 or str(parsed) != str(value).lower():
        raise ValueError("invalid reference")
    return parsed


def _bounded_text(value: str, field: str) -> str:
    if value != value.strip() or not value or "\x00" in value:
        raise ValueError(f"invalid {field}")
    return value


class _StrictChapterModel(BaseModel):
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
        "allowed_segments",
        "target_segment_ids",
        "feedback_refs",
        "review_report_refs",
        "segments",
        "self_check",
        "uncertainty_markers",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def immutable_collections(cls, value: object) -> object:
        if type(value) is list:
            return tuple(value)
        return value


class ApprovedOutlineReference(_StrictChapterModel):
    """An approved outline identity; provider-controlled paths are intentionally absent."""

    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID


class SourceDraftSegment(_StrictChapterModel):
    """Bounded source prose supplied by the orchestrator without lookup authority."""

    segment_id: UUID
    index: int = Field(ge=1, le=1024)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=32_768, repr=False)

    @field_validator("title", "content")
    @classmethod
    def valid_text(cls, value: str, info: object) -> str:
        value = _bounded_text(value, getattr(info, "field_name", "text"))
        if getattr(info, "field_name", None) == "content" and len(value.encode("utf-8")) > 32_768:
            raise ValueError("source draft segment content is too large")
        return value


class SourceDraftReference(_StrictChapterModel):
    """The exact candidate source version a revision is allowed to replace."""

    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
    segments: tuple[SourceDraftSegment, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_ordered_segments(self) -> Self:
        ids = [item.segment_id for item in self.segments]
        indexes = [item.index for item in self.segments]
        if len(ids) != len(set(ids)) or indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("invalid source draft segment references")
        return self


class AllowedChapterSegment(_StrictChapterModel):
    segment_id: UUID
    index: int = Field(ge=1, le=1024)
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(min_length=1, max_length=8000, repr=False)

    @field_validator("title", "brief")
    @classmethod
    def valid_text(cls, value: str, info: object) -> str:
        return _bounded_text(value, getattr(info, "field_name", "text"))


class UserFeedbackReference(_StrictChapterModel):
    feedback_id: UUID
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    source_draft_document_id: UUID
    source_draft_version_id: UUID
    instruction: str = Field(min_length=1, max_length=8000, repr=False)

    @field_validator("instruction")
    @classmethod
    def valid_instruction(cls, value: str) -> str:
        return _bounded_text(value, "feedback")


class ReviewReportReference(_StrictChapterModel):
    report_id: UUID
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    target_draft_document_id: UUID
    target_draft_version_id: UUID
    summary: str = Field(min_length=1, max_length=8000, repr=False)

    @field_validator("summary")
    @classmethod
    def valid_summary(cls, value: str) -> str:
        return _bounded_text(value, "review summary")


class _DraftRequest(_StrictChapterModel):
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    approved_outline: ApprovedOutlineReference
    allowed_segments: tuple[AllowedChapterSegment, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def consistent_bindings(self) -> Self:
        if (self.approved_outline.project_id, self.approved_outline.chapter_id) != (
            self.project_id,
            self.chapter_id,
        ):
            raise ValueError("cross-project outline reference")
        ids = [item.segment_id for item in self.allowed_segments]
        indexes = [item.index for item in self.allowed_segments]
        if len(ids) != len(set(ids)) or len(indexes) != len(set(indexes)):
            raise ValueError("duplicate or contradictory segment reference")
        if indexes != list(range(1, len(indexes) + 1)):
            raise ValueError("segments are not contiguous canonical order")
        envelope_bytes = len(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        if envelope_bytes > MAX_CHAPTER_REQUEST_ENVELOPE_BYTES:
            raise ValueError("chapter writer request is too large")
        return self

    def _validate_targets(self, targets: tuple[UUID, ...]) -> None:
        if not targets or len(targets) != len(set(targets)):
            raise ValueError("invalid target segment references")
        known = {item.segment_id for item in self.allowed_segments}
        if not set(targets) <= known:
            raise ValueError("unknown target segment reference")
        canonical_targets = tuple(
            item.segment_id for item in self.allowed_segments if item.segment_id in targets
        )
        if targets != canonical_targets:
            raise ValueError("target segments are not in canonical order")


class InitialDraftRequest(_DraftRequest):
    @property
    def target_segment_ids(self) -> tuple[UUID, ...]:
        return tuple(item.segment_id for item in self.allowed_segments)


class SegmentDraftRequest(_DraftRequest):
    target_segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_targets(self) -> Self:
        self._validate_targets(self.target_segment_ids)
        return self


class _RevisionRequest(_DraftRequest):
    source_draft: SourceDraftReference
    target_segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_revision_bindings(self) -> Self:
        if (self.source_draft.project_id, self.source_draft.chapter_id) != (
            self.project_id,
            self.chapter_id,
        ):
            raise ValueError("cross-project source draft reference")
        if len(self.source_draft.segments) != len(self.allowed_segments) or any(
            (source.segment_id, source.index, source.title)
            != (allowed.segment_id, allowed.index, allowed.title)
            for source, allowed in zip(
                self.source_draft.segments, self.allowed_segments, strict=True
            )
        ):
            raise ValueError("source draft segments do not match the approved outline")
        self._validate_targets(self.target_segment_ids)
        return self


class UserFeedbackRevisionRequest(_RevisionRequest):
    feedback_refs: tuple[UserFeedbackReference, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def valid_feedback(self) -> Self:
        ids = [item.feedback_id for item in self.feedback_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate feedback reference")
        lineage = (
            self.project_id,
            self.chapter_id,
            self.workflow_run_id,
            self.source_draft.document_id,
            self.source_draft.version_id,
        )
        if any(
            (
                item.project_id,
                item.chapter_id,
                item.workflow_run_id,
                item.source_draft_document_id,
                item.source_draft_version_id,
            )
            != lineage
            for item in self.feedback_refs
        ):
            raise ValueError("cross-project or stale feedback reference")
        return self


class ReviewDrivenRevisionRequest(_RevisionRequest):
    review_report_refs: tuple[ReviewReportReference, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def valid_reports(self) -> Self:
        ids = [item.report_id for item in self.review_report_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate review report reference")
        lineage = (
            self.project_id,
            self.chapter_id,
            self.workflow_run_id,
            self.source_draft.document_id,
            self.source_draft.version_id,
        )
        if any(
            (
                item.project_id,
                item.chapter_id,
                item.workflow_run_id,
                item.target_draft_document_id,
                item.target_draft_version_id,
            )
            != lineage
            for item in self.review_report_refs
        ):
            raise ValueError("cross-project or stale review report reference")
        return self


ChapterWriterRequest = (
    InitialDraftRequest
    | SegmentDraftRequest
    | UserFeedbackRevisionRequest
    | ReviewDrivenRevisionRequest
)


class CandidateChapterSegment(_StrictChapterModel):
    segment_id: UUID
    index: int = Field(ge=1, le=1024)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=262_144, repr=False)

    @field_validator("title", "content")
    @classmethod
    def valid_text(cls, value: str, info: object) -> str:
        value = _bounded_text(value, getattr(info, "field_name", "text"))
        if getattr(info, "field_name", None) == "content" and len(value.encode("utf-8")) > 262_144:
            raise ValueError("candidate segment content is too large")
        return value


class CandidateSelfCheck(_StrictChapterModel):
    outline_followed: bool
    allowed_segments_only: bool
    continuity_checked: bool
    notes: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("notes", mode="before")
    @classmethod
    def immutable_notes(cls, value: object) -> object:
        if type(value) is list:
            return tuple(value)
        return value

    @field_validator("notes")
    @classmethod
    def valid_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item.encode("utf-8")) > 500 or _bounded_text(item, "note") != item for item in value
        ):
            raise ValueError("invalid self-check note")
        if len(value) != len(set(value)):
            raise ValueError("duplicate self-check note")
        return value


class CandidateChapterOutput(_StrictChapterModel):
    """A non-canonical candidate. Persistence authority is deliberately absent."""

    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    approved_outline_document_id: UUID
    approved_outline_version_id: UUID
    source_draft_document_id: UUID | None = None
    source_draft_version_id: UUID | None = None
    complete_chapter: bool
    segments: tuple[CandidateChapterSegment, ...] = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=4000)
    self_check: CandidateSelfCheck
    uncertainty_markers: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("summary")
    @classmethod
    def valid_summary(cls, value: str) -> str:
        return _bounded_text(value, "summary")

    @field_validator("uncertainty_markers")
    @classmethod
    def valid_markers(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        if any(len(item) > 500 or _bounded_text(item, "marker") != item for item in value):
            raise ValueError(f"invalid {getattr(info, 'field_name', 'markers')}")
        if len(value) != len(set(value)):
            raise ValueError("duplicate marker")
        return value

    @model_validator(mode="after")
    def unique_ordered_segments(self) -> Self:
        ids = [item.segment_id for item in self.segments]
        indexes = [item.index for item in self.segments]
        if (
            len(ids) != len(set(ids))
            or len(indexes) != len(set(indexes))
            or indexes != sorted(indexes)
        ):
            raise ValueError("duplicate or contradictory candidate segments")
        content_bytes = sum(len(item.content.encode("utf-8")) for item in self.segments)
        envelope_bytes = len(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        if (
            content_bytes > MAX_CANDIDATE_CONTENT_BYTES
            or envelope_bytes > MAX_CANDIDATE_ENVELOPE_BYTES
        ):
            raise ValueError("candidate output is too large")
        return self


def validate_candidate_chapter_output(
    raw_output: object, *, request: ChapterWriterRequest
) -> CandidateChapterOutput:
    result: CandidateChapterOutput | None = None
    try:
        if isinstance(raw_output, BaseModel):
            if type(raw_output) is not CandidateChapterOutput:
                raise TypeError("provider output must use the candidate output envelope")
            raw_output = raw_output.model_dump(mode="json")
        result = CandidateChapterOutput.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        pass
    if result is None:
        raise ProviderInvalidOutputError() from None

    source_version = (
        request.source_draft.version_id if isinstance(request, _RevisionRequest) else None
    )
    source_document = (
        request.source_draft.document_id if isinstance(request, _RevisionRequest) else None
    )
    expected_lineage = (
        request.project_id,
        request.chapter_id,
        request.workflow_run_id,
        request.approved_outline.document_id,
        request.approved_outline.version_id,
        source_document,
        source_version,
    )
    actual_lineage = (
        result.project_id,
        result.chapter_id,
        result.workflow_run_id,
        result.approved_outline_document_id,
        result.approved_outline_version_id,
        result.source_draft_document_id,
        result.source_draft_version_id,
    )
    allowed = {item.segment_id: item for item in request.allowed_segments}
    target_ids = request.target_segment_ids
    if (
        actual_lineage != expected_lineage
        or len(result.segments) != len(target_ids)
        or tuple(item.segment_id for item in result.segments) != target_ids
        or result.complete_chapter is not isinstance(request, InitialDraftRequest)
    ):
        raise ProviderInvalidOutputError() from None
    for segment in result.segments:
        expected = allowed.get(segment.segment_id)
        if expected is None or (segment.index, segment.title) != (expected.index, expected.title):
            raise ProviderInvalidOutputError() from None
    return result


__all__ = [
    "AllowedChapterSegment",
    "ApprovedOutlineReference",
    "CandidateChapterOutput",
    "CandidateChapterSegment",
    "CandidateSelfCheck",
    "ChapterWriterRequest",
    "InitialDraftRequest",
    "ReviewDrivenRevisionRequest",
    "ReviewReportReference",
    "SegmentDraftRequest",
    "SourceDraftReference",
    "SourceDraftSegment",
    "UserFeedbackReference",
    "UserFeedbackRevisionRequest",
    "validate_candidate_chapter_output",
]
