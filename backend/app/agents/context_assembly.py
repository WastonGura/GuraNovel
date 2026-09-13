"""Deterministic context assembly, budgeting, and material sufficiency verification.

Strictly prioritizes materials, validates version bindings and project boundaries,
enforces token budgets without silent drops, and records privacy-safe provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    InitialDraftRequest,
    ReviewDrivenRevisionRequest,
    SegmentDraftRequest,
    SourceDraftSegment,
    UserFeedbackRevisionRequest,
    WriterContextKind,
    WriterContextSnapshot,
)
from app.agents.chapter_review_contracts import (
    ChapterReviewRequest,
    ChapterReviewTarget,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewerRole,
)
from app.agents.profiles import AgentProfile


class ContextAssemblyError(Exception):
    """Base error for context assembly and budgeting failures."""


class ContextInsufficientError(ContextAssemblyError):
    """Raised when essential materials (outline text, draft, feedback) are missing."""


class ContextBudgetExceededError(ContextAssemblyError):
    """Raised when required constraints exceed the token budget."""


class ContextIsolationError(ContextAssemblyError):
    """Raised when cross-project, unallowlisted, or unknown version contexts are detected."""


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate for multilingual/Chinese and English prose."""
    if not text:
        return 0
    return max(1, len(text) // 2 + 1)


def _content_fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ContextProvenance:
    """Publicly safe execution provenance metadata. Never logs raw prose."""

    profile_name: str
    profile_mode: str | None
    profile_version: str
    system_prompt_fingerprint: str
    outline_version_id: str
    outline_content_hash: str
    source_draft_version_id: str | None
    included_context_document_ids: tuple[str, ...]
    excluded_context_document_ids: tuple[str, ...]
    estimated_total_tokens: int
    budget_limit_tokens: int


@dataclass(frozen=True)
class AssembledWriterContext:
    profile: AgentProfile
    request: (
        InitialDraftRequest
        | SegmentDraftRequest
        | UserFeedbackRevisionRequest
        | ReviewDrivenRevisionRequest
    )
    system_prompt: str
    outline_content: str
    active_segments: tuple[AllowedChapterSegment, ...]
    source_segments: tuple[SourceDraftSegment, ...]
    auxiliary_contexts: tuple[WriterContextSnapshot, ...]
    provenance: ContextProvenance


@dataclass(frozen=True)
class AssembledReviewContext:
    profile: AgentProfile
    request: ChapterReviewRequest
    system_prompt: str
    outline_content: str
    target: ChapterReviewTarget
    auxiliary_contexts: tuple[ReviewContextSnapshot, ...]
    provenance: ContextProvenance


_WRITER_CONTEXT_PRIORITY: dict[WriterContextKind, int] = {
    WriterContextKind.STYLE_GUIDE: 1,
    WriterContextKind.PREVIOUS_CHAPTER_SUMMARY: 2,
    WriterContextKind.CHARACTER_STATE: 3,
    WriterContextKind.LORE_BOUNDARY: 4,
    WriterContextKind.TIMELINE: 5,
}

_REVIEW_CONTEXT_PRIORITY: dict[tuple[ReviewerRole, ReviewContextKind], int] = {
    (ReviewerRole.EDITOR, ReviewContextKind.STYLE_GUIDE): 1,
    (ReviewerRole.EDITOR, ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY): 2,
    (ReviewerRole.CHIEF_EDITOR, ReviewContextKind.AUDIENCE_GOAL): 1,
    (ReviewerRole.CHIEF_EDITOR, ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY): 2,
    (ReviewerRole.CHIEF_EDITOR, ReviewContextKind.STYLE_GUIDE): 3,
    (ReviewerRole.LORE, ReviewContextKind.LORE_BOUNDARY): 1,
    (ReviewerRole.LORE, ReviewContextKind.CHARACTER_STATE): 2,
    (ReviewerRole.LORE, ReviewContextKind.TIMELINE): 3,
    (ReviewerRole.LORE, ReviewContextKind.FORESHADOWING): 4,
}


def assemble_writer_context(
    request: (
        InitialDraftRequest
        | SegmentDraftRequest
        | UserFeedbackRevisionRequest
        | ReviewDrivenRevisionRequest
    ),
    profile: AgentProfile,
) -> AssembledWriterContext:
    # 1. Verify Outline Material Sufficiency
    outline_content = request.approved_outline.content.strip()
    if not outline_content:
        raise ContextInsufficientError("approved outline content is required and cannot be empty")

    # 2. Verify Segment Sufficiency
    if not request.allowed_segments:
        raise ContextInsufficientError("allowed segments cannot be empty")

    # 3. Verify Revision Sufficiency
    source_segments: tuple[SourceDraftSegment, ...] = ()
    source_draft_version: str | None = None
    if isinstance(request, (UserFeedbackRevisionRequest, ReviewDrivenRevisionRequest)) or hasattr(request, "source_draft"):
        source_draft = getattr(request, "source_draft", None)
        if not source_draft or not getattr(source_draft, "segments", ()):
            raise ContextInsufficientError("source draft segments are required for revision")
        source_segments = getattr(source_draft, "segments", ())
        source_draft_version = str(getattr(source_draft, "version_id", None))

    if isinstance(request, UserFeedbackRevisionRequest) or hasattr(request, "feedback_refs"):
        feedback_refs = getattr(request, "feedback_refs", ())
        if not feedback_refs or any(not getattr(item, "instruction", "").strip() for item in feedback_refs):
            raise ContextInsufficientError("user feedback instruction is required")

    if isinstance(request, ReviewDrivenRevisionRequest) or hasattr(request, "review_report_refs"):
        review_report_refs = getattr(request, "review_report_refs", ())
        if not review_report_refs:
            raise ContextInsufficientError("review report references are required")

    # 4. Verify Project Isolation
    project_id = request.project_id
    if request.approved_outline.project_id != project_id:
        raise ContextIsolationError("approved outline project mismatch")
    for ctx in request.contexts:
        if ctx.project_id != project_id:
            raise ContextIsolationError(f"context {ctx.document_id} project mismatch")

    # 5. Invariant Token Calculation
    invariant_text_parts = [
        profile.system_prompt,
        outline_content,
        " ".join(item.brief for item in request.allowed_segments),
        " ".join(item.content for item in source_segments),
    ]
    if isinstance(request, UserFeedbackRevisionRequest):
        invariant_text_parts.extend(item.instruction for item in request.feedback_refs)
    elif isinstance(request, ReviewDrivenRevisionRequest):
        invariant_text_parts.extend(item.summary for item in request.review_report_refs)
        invariant_text_parts.extend(
            f"{item.finding.rationale} {item.finding.suggested_action}"
            for item in request.selected_findings
        )

    invariant_tokens = sum(_estimate_tokens(part) for part in invariant_text_parts)
    budget_limit = profile.context_policy.max_context_tokens

    if invariant_tokens > budget_limit:
        raise ContextBudgetExceededError(
            f"mandatory constraints ({invariant_tokens} tokens) exceed profile budget ({budget_limit} tokens)"
        )

    # 6. Prioritize & Budget Auxiliary Contexts
    sorted_contexts = sorted(
        request.contexts,
        key=lambda c: _WRITER_CONTEXT_PRIORITY.get(c.kind, 99),
    )

    included_contexts: list[WriterContextSnapshot] = []
    included_ids: list[str] = []
    excluded_ids: list[str] = []
    total_tokens = invariant_tokens

    required_context_names = set(profile.context_policy.required)

    for ctx in sorted_contexts:
        ctx_tokens = _estimate_tokens(ctx.content)
        is_required = ctx.kind.value in required_context_names
        if total_tokens + ctx_tokens <= budget_limit:
            included_contexts.append(ctx)
            included_ids.append(str(ctx.document_id))
            total_tokens += ctx_tokens
        else:
            if is_required:
                raise ContextBudgetExceededError(
                    f"required context {ctx.kind.value} cannot fit within remaining budget"
                )
            excluded_ids.append(str(ctx.document_id))

    provenance = ContextProvenance(
        profile_name=profile.name,
        profile_mode=profile.mode,
        profile_version=profile.version,
        system_prompt_fingerprint=_content_fingerprint(profile.system_prompt),
        outline_version_id=str(request.approved_outline.version_id),
        outline_content_hash=_content_fingerprint(outline_content),
        source_draft_version_id=source_draft_version,
        included_context_document_ids=tuple(included_ids),
        excluded_context_document_ids=tuple(excluded_ids),
        estimated_total_tokens=total_tokens,
        budget_limit_tokens=budget_limit,
    )

    return AssembledWriterContext(
        profile=profile,
        request=request,
        system_prompt=profile.system_prompt,
        outline_content=outline_content,
        active_segments=request.allowed_segments,
        source_segments=source_segments,
        auxiliary_contexts=tuple(included_contexts),
        provenance=provenance,
    )


def assemble_review_context(
    request: ChapterReviewRequest,
    profile: AgentProfile,
    role: ReviewerRole,
) -> AssembledReviewContext:
    # 1. Verify Outline Sufficiency
    outline_content = request.approved_outline.content.strip()
    if not outline_content:
        raise ContextInsufficientError("approved outline content is required and cannot be empty")

    # 2. Verify Target Sufficiency
    if not request.target.segments or any(not s.content.strip() for s in request.target.segments):
        raise ContextInsufficientError("target chapter segments are required and cannot be empty")

    # 3. Verify Project and Role Isolation
    project_id = request.project_id
    if request.target.project_id != project_id or request.approved_outline.project_id != project_id:
        raise ContextIsolationError("target or outline project mismatch")

    allowed_kinds = request._ALLOWED_CONTEXTS
    for ctx in request.contexts:
        if ctx.project_id != project_id:
            raise ContextIsolationError(f"context {ctx.document_id} project mismatch")
        if ctx.kind not in allowed_kinds:
            raise ContextIsolationError(f"context kind {ctx.kind} not allowed for {role.value}")

    # 4. Invariant Token Calculation
    invariant_text_parts = [
        profile.system_prompt,
        outline_content,
        " ".join(item.content for item in request.target.segments),
    ]
    invariant_tokens = sum(_estimate_tokens(part) for part in invariant_text_parts)
    budget_limit = profile.context_policy.max_context_tokens

    if invariant_tokens > budget_limit:
        raise ContextBudgetExceededError(
            f"mandatory review constraints ({invariant_tokens} tokens) exceed profile budget ({budget_limit} tokens)"
        )

    # 5. Prioritize & Budget Auxiliary Contexts
    sorted_contexts = sorted(
        request.contexts,
        key=lambda c: _REVIEW_CONTEXT_PRIORITY.get((role, c.kind), 99),
    )

    included_contexts: list[ReviewContextSnapshot] = []
    included_ids: list[str] = []
    excluded_ids: list[str] = []
    total_tokens = invariant_tokens

    for ctx in sorted_contexts:
        ctx_tokens = _estimate_tokens(ctx.content)
        if total_tokens + ctx_tokens <= budget_limit:
            included_contexts.append(ctx)
            included_ids.append(str(ctx.document_id))
            total_tokens += ctx_tokens
        else:
            excluded_ids.append(str(ctx.document_id))

    provenance = ContextProvenance(
        profile_name=profile.name,
        profile_mode=profile.mode,
        profile_version=profile.version,
        system_prompt_fingerprint=_content_fingerprint(profile.system_prompt),
        outline_version_id=str(request.approved_outline.version_id),
        outline_content_hash=_content_fingerprint(outline_content),
        source_draft_version_id=str(request.target.version_id),
        included_context_document_ids=tuple(included_ids),
        excluded_context_document_ids=tuple(excluded_ids),
        estimated_total_tokens=total_tokens,
        budget_limit_tokens=budget_limit,
    )

    return AssembledReviewContext(
        profile=profile,
        request=request,
        system_prompt=profile.system_prompt,
        outline_content=outline_content,
        target=request.target,
        auxiliary_contexts=tuple(included_contexts),
        provenance=provenance,
    )


__all__ = [
    "AssembledReviewContext",
    "AssembledWriterContext",
    "ContextAssemblyError",
    "ContextBudgetExceededError",
    "ContextInsufficientError",
    "ContextIsolationError",
    "ContextProvenance",
    "assemble_review_context",
    "assemble_writer_context",
]
