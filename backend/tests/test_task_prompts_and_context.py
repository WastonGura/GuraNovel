"""Tests for task prompts, version-bound chapter context assembly, budgeting, and quality benchmarks."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID
import pytest
from pydantic import ValidationError

from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    ApprovedOutlineSnapshot,
    CandidateChapterOutput,
    ChapterReviewFinding,
    ChapterReviewReport,
    ChapterReviewTarget,
    ChiefEditorChapterFinalAgent,
    ChiefEditorChapterFinalRequest,
    EditorAgent,
    EditorReviewRequest,
    InitialDraftRequest,
    LoreChapterFinalAgent,
    LoreChapterFinalRequest,
    ProfileRegistry,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewDrivenRevisionRequest,
    ReviewFindingSeverity,
    ReviewReportReference,
    ReviewSegmentSnapshot,
    ReviewerRole,
    RevisionAgent,
    SegmentDraftRequest,
    SelectedReviewFinding,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
    WriterAgent,
    WriterContextKind,
    WriterContextSnapshot,
)
from app.agents.capture_provider import (
    CaptureChapterReviewProvider,
    CaptureChapterWriterProvider,
)
from app.agents.context_assembly import (
    ContextBudgetExceededError,
    ContextInsufficientError,
    ContextIsolationError,
    assemble_review_context,
    assemble_writer_context,
)
from tests.quality_benchmarks import (
    ALL_QUALITY_BENCHMARKS,
    HUMAN_EVALUATION_RUBRIC,
    BenchmarkFlawType,
    EvaluationDimension,
)


PROJECT_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_PROJECT_ID = UUID("10000000-0000-4000-8000-000000000002")
CHAPTER_ID = UUID("20000000-0000-4000-8000-000000000001")
RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
OUTLINE_DOC_ID = UUID("40000000-0000-4000-8000-000000000001")
OUTLINE_VER_ID = UUID("40000000-0000-4000-8000-000000000002")
DRAFT_DOC_ID = UUID("50000000-0000-4000-8000-000000000001")
DRAFT_VER_ID = UUID("50000000-0000-4000-8000-000000000002")
SEG_1_ID = UUID("60000000-0000-4000-8000-000000000001")
SEG_2_ID = UUID("60000000-0000-4000-8000-000000000002")


def _outline_ref(content: str = "第十四章 雾巷钟楼：林野避开凡斯搜捕，取得黄铜机芯内的秘密。") -> ApprovedOutlineReference:
    return ApprovedOutlineReference(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=OUTLINE_DOC_ID,
        version_id=OUTLINE_VER_ID,
        content=content,
    )


def _outline_snapshot(content: str = "第十四章 雾巷钟楼：林野避开凡斯搜捕，取得黄铜机芯内的秘密。") -> ApprovedOutlineSnapshot:
    return ApprovedOutlineSnapshot(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=OUTLINE_DOC_ID,
        version_id=OUTLINE_VER_ID,
        content=content,
    )


def _allowed_segments() -> tuple[AllowedChapterSegment, ...]:
    return (
        AllowedChapterSegment(
            segment_id=SEG_1_ID,
            index=1,
            title="潜入雾巷",
            brief="林野在雨雾中避开凡斯督察的巡警提灯，潜入钟楼侧门。",
        ),
        AllowedChapterSegment(
            segment_id=SEG_2_ID,
            index=2,
            title="机芯秘密",
            brief="林野登上顶层，在黄铜齿轮中取出密码筒，发现第三人痕迹。",
        ),
    )


def _source_draft() -> SourceDraftReference:
    return SourceDraftReference(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DRAFT_DOC_ID,
        version_id=DRAFT_VER_ID,
        segments=[
            SourceDraftSegment(
                segment_id=SEG_1_ID,
                index=1,
                title="潜入雾巷",
                content="冷雨洗刷着老城石板路。林野避过巡警提灯，闪身潜入钟楼。",
            ),
            SourceDraftSegment(
                segment_id=SEG_2_ID,
                index=2,
                title="机芯秘密",
                content="他在黄铜齿轮背后摸到了密码筒，却看见了一枚新鲜的湿泥鞋印。",
            ),
        ],
    )


def _sample_writer_contexts() -> tuple[WriterContextSnapshot, ...]:
    return (
        WriterContextSnapshot(
            document_id=UUID("70000000-0000-4000-8000-000000000001"),
            version_id=UUID("70000000-0000-4000-8000-000000000002"),
            project_id=PROJECT_ID,
            kind=WriterContextKind.STYLE_GUIDE,
            content="基调：严肃维多利亚蒸汽悬疑。禁止现代网络梗，强化雨雾与黄铜冷硬感官描摹。",
        ),
        WriterContextSnapshot(
            document_id=UUID("80000000-0000-4000-8000-000000000001"),
            version_id=UUID("80000000-0000-4000-8000-000000000002"),
            project_id=PROJECT_ID,
            kind=WriterContextKind.CHARACTER_STATE,
            content="林野：被陷害的前王家侦探，意志沉着，绝不无端屈服认罪。",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Capture Provider & Boundary Delivery Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_writer_initial_draft_reaches_boundary_with_actual_content() -> None:
    provider = CaptureChapterWriterProvider()
    agent = WriterAgent(provider=provider)
    request = InitialDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
        contexts=_sample_writer_contexts(),
    )

    output = await agent.initial_draft(request)

    assert isinstance(output, CandidateChapterOutput)
    assert len(provider.invocations) == 1
    inv = provider.invocations[0]

    assert inv.role == "writer"
    assert inv.profile_name == "writer_agent"
    assert inv.profile_mode == "initial_draft"
    # Verify rich prompt instructions arrived
    assert "Point of View (POV)" in inv.system_prompt
    assert "Character Agency & Motivation" in inv.system_prompt
    assert "Scene Goals & Dramatic Arc" in inv.system_prompt
    # Verify actual outline text arrived at model boundary
    assert "第十四章 雾巷钟楼" in inv.outline_content
    # Verify auxiliary contexts arrived
    assert "style_guide" in inv.context_kinds
    assert "character_state" in inv.context_kinds
    assert inv.total_tokens_estimate > 0
    # Verify privacy safety in provenance
    assert inv.provenance.outline_version_id == str(OUTLINE_VER_ID)
    assert inv.provenance.outline_content_hash != ""


@pytest.mark.anyio
async def test_writer_segment_draft_reaches_boundary() -> None:
    provider = CaptureChapterWriterProvider()
    agent = WriterAgent(provider=provider)
    request = SegmentDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
        target_segment_ids=[SEG_2_ID],
        contexts=_sample_writer_contexts(),
    )

    output = await agent.segment_draft(request)

    assert isinstance(output, CandidateChapterOutput)
    assert len(provider.invocations) == 1
    inv = provider.invocations[0]
    assert inv.profile_name == "writer_agent"
    assert inv.profile_mode == "segment_draft"
    assert "Seamless Continuity" in inv.system_prompt
    assert "Character Voice & Agency" in inv.system_prompt


@pytest.mark.anyio
async def test_revision_user_feedback_reaches_boundary() -> None:
    provider = CaptureChapterWriterProvider()
    agent = RevisionAgent(provider=provider)
    feedback_id = UUID("90000000-0000-4000-8000-000000000001")
    request = UserFeedbackRevisionRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        source_draft=_source_draft(),
        allowed_segments=_allowed_segments(),
        target_segment_ids=[SEG_2_ID],
        feedback_refs=[
            UserFeedbackReference(
                feedback_id=feedback_id,
                project_id=PROJECT_ID,
                chapter_id=CHAPTER_ID,
                workflow_run_id=RUN_ID,
                source_draft_document_id=DRAFT_DOC_ID,
                source_draft_version_id=DRAFT_VER_ID,
                instruction="加深林野发现陌生鞋印时的警惕心理与环境雨水细节。",
            )
        ],
        contexts=_sample_writer_contexts(),
    )

    output = await agent.user_feedback_revision(request)

    assert isinstance(output, CandidateChapterOutput)
    assert len(provider.invocations) == 1
    inv = provider.invocations[0]
    assert inv.profile_name == "revision_agent"
    assert inv.profile_mode == "user_feedback_revision"
    assert "Surgical Focus" in inv.system_prompt
    assert "Non-Target Preservation" in inv.system_prompt


@pytest.mark.anyio
async def test_revision_review_driven_reaches_boundary() -> None:
    provider = CaptureChapterWriterProvider()
    agent = RevisionAgent(provider=provider)
    report_id = UUID("a0000000-0000-4000-8000-000000000001")
    finding = ChapterReviewFinding(
        sequence=1,
        code="sensory_thin",
        severity=ReviewFindingSeverity.WARNING,
        required=False,
        evidence_segment_ids=[SEG_1_ID],
        rationale="氛围描写单薄",
        suggested_action="增加老城区雨夜音画细节",
    )
    request = ReviewDrivenRevisionRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        source_draft=_source_draft(),
        allowed_segments=_allowed_segments(),
        target_segment_ids=[SEG_1_ID],
        review_report_refs=[
            ReviewReportReference(
                report_id=report_id,
                project_id=PROJECT_ID,
                chapter_id=CHAPTER_ID,
                workflow_run_id=RUN_ID,
                target_draft_document_id=DRAFT_DOC_ID,
                target_draft_version_id=DRAFT_VER_ID,
                summary="第1段雨雾氛围描摹稍显单薄，建议补充煤气提灯与蒸汽机车轰鸣的远景背景。",
            )
        ],
        selected_findings=[
            SelectedReviewFinding(
                report_id=report_id,
                finding=finding,
            )
        ],
        contexts=_sample_writer_contexts(),
    )

    output = await agent.review_driven_revision(request)

    assert isinstance(output, CandidateChapterOutput)
    assert len(provider.invocations) == 1
    inv = provider.invocations[0]
    assert inv.profile_name == "revision_agent"
    assert inv.profile_mode == "review_driven_revision"
    assert "Selected Findings Authority" in inv.system_prompt
    assert "Finding Severity Guidance" in inv.system_prompt


@pytest.mark.anyio
async def test_editor_review_reaches_boundary() -> None:
    provider = CaptureChapterReviewProvider()
    agent = EditorAgent(provider=provider)
    request = EditorReviewRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=ChapterReviewTarget(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DRAFT_DOC_ID,
            version_id=DRAFT_VER_ID,
            segments=[
                ReviewSegmentSnapshot(
                    segment_id=SEG_1_ID,
                    index=1,
                    title="潜入雾巷",
                    content="冷雨洗刷着老城石板路。林野避过巡警提灯，闪身潜入钟楼。",
                )
            ],
        ),
        approved_outline=_outline_snapshot(),
        contexts=[
            ReviewContextSnapshot(
                document_id=UUID("70000000-0000-4000-8000-000000000001"),
                version_id=UUID("70000000-0000-4000-8000-000000000002"),
                project_id=PROJECT_ID,
                kind=ReviewContextKind.STYLE_GUIDE,
                content="严禁现代网络梗，行文冷峻典雅。",
            )
        ],
    )

    report = await agent.review(request)

    assert isinstance(report, ChapterReviewReport)
    assert len(provider.invocations) == 1
    inv = provider.invocations[0]
    assert inv.role == "editor_agent"
    assert inv.profile_name == "editor_agent"
    assert inv.profile_mode is None
    assert "Core Editorial Focus" in inv.system_prompt
    assert "Finding Severity Standards" in inv.system_prompt


@pytest.mark.anyio
async def test_chief_editor_and_lore_review_reaches_boundary() -> None:
    chief_provider = CaptureChapterReviewProvider()
    chief_agent = ChiefEditorChapterFinalAgent(provider=chief_provider)
    chief_req = ChiefEditorChapterFinalRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=ChapterReviewTarget(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DRAFT_DOC_ID,
            version_id=DRAFT_VER_ID,
            segments=[
                ReviewSegmentSnapshot(
                    segment_id=SEG_1_ID,
                    index=1,
                    title="潜入雾巷",
                    content="冷雨洗刷着老城石板路。林野避过巡警提灯，闪身潜入钟楼。",
                )
            ],
        ),
        approved_outline=_outline_snapshot(),
        contexts=[
            ReviewContextSnapshot(
                document_id=UUID("99000000-0000-4000-8000-000000000001"),
                version_id=UUID("99000000-0000-4000-8000-000000000002"),
                project_id=PROJECT_ID,
                kind=ReviewContextKind.AUDIENCE_GOAL,
                content="受众期待：严密侦探悬疑。",
            )
        ],
    )
    chief_report = await chief_agent.review(chief_req)
    assert isinstance(chief_report, ChapterReviewReport)
    assert chief_provider.invocations[0].role == "chief_editor_agent"
    assert chief_provider.invocations[0].profile_name == "chief_editor"
    assert chief_provider.invocations[0].profile_mode == "chapter_final"
    assert "Strategic Reader Value" in chief_provider.invocations[0].system_prompt

    lore_provider = CaptureChapterReviewProvider()
    lore_agent = LoreChapterFinalAgent(provider=lore_provider)
    lore_req = LoreChapterFinalRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=ChapterReviewTarget(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DRAFT_DOC_ID,
            version_id=DRAFT_VER_ID,
            segments=[
                ReviewSegmentSnapshot(
                    segment_id=SEG_1_ID,
                    index=1,
                    title="潜入雾巷",
                    content="冷雨洗刷着老城石板路。林野避过巡警提灯，闪身潜入钟楼。",
                )
            ],
        ),
        approved_outline=_outline_snapshot(),
        contexts=[
            ReviewContextSnapshot(
                document_id=UUID("88000000-0000-4000-8000-000000000001"),
                version_id=UUID("88000000-0000-4000-8000-000000000002"),
                project_id=PROJECT_ID,
                kind=ReviewContextKind.LORE_BOUNDARY,
                content="日蚀纪元 100 年：凡恩大执政官殉职五周年。",
            )
        ],
    )
    lore_report = await lore_agent.review(lore_req)
    assert isinstance(lore_report, ChapterReviewReport)
    assert lore_provider.invocations[0].role == "lore_agent"
    assert lore_provider.invocations[0].profile_name == "lore_agent"
    assert lore_provider.invocations[0].profile_mode == "chapter_final"
    assert "Canon Fidelity" in lore_provider.invocations[0].system_prompt


# ---------------------------------------------------------------------------
# 2. Context Sufficiency & Invariant Material Verification
# ---------------------------------------------------------------------------


def test_context_insufficient_when_outline_content_empty() -> None:
    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")

    empty_outline = ApprovedOutlineReference(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=OUTLINE_DOC_ID,
        version_id=OUTLINE_VER_ID,
        content="",  # empty content
    )
    request = InitialDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=empty_outline,
        allowed_segments=_allowed_segments(),
    )

    with pytest.raises(ContextInsufficientError, match="approved outline content is required"):
        assemble_writer_context(request, profile)


def test_context_insufficient_when_review_target_empty() -> None:
    registry = ProfileRegistry()
    profile = registry.load("editor_agent", None)

    mock_request = SimpleNamespace(
        project_id=PROJECT_ID,
        approved_outline=_outline_snapshot(),
        target=SimpleNamespace(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DRAFT_DOC_ID,
            version_id=DRAFT_VER_ID,
            segments=(),  # empty segments
        ),
        _ALLOWED_CONTEXTS=EditorReviewRequest._ALLOWED_CONTEXTS,
        contexts=(),
    )

    with pytest.raises(ContextInsufficientError, match="target chapter segments are required"):
        assemble_review_context(mock_request, profile, ReviewerRole.EDITOR)  # type: ignore[arg-type]


def test_context_insufficient_when_feedback_instruction_missing() -> None:
    registry = ProfileRegistry()
    profile = registry.load("revision_agent", "user_feedback_revision")

    # A mock/tampered request where instruction is empty
    mock_request = SimpleNamespace(
        project_id=PROJECT_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
        source_draft=_source_draft(),
        feedback_refs=[SimpleNamespace(instruction="   ")],
        contexts=(),
    )

    with pytest.raises(ContextInsufficientError, match="user feedback instruction is required"):
        assemble_writer_context(mock_request, profile)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Project Isolation & Boundary Defense
# ---------------------------------------------------------------------------


def test_context_isolation_rejects_cross_project_outline() -> None:
    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")

    foreign_outline = ApprovedOutlineReference(
        project_id=OTHER_PROJECT_ID,  # Foreign project!
        chapter_id=CHAPTER_ID,
        document_id=OUTLINE_DOC_ID,
        version_id=OUTLINE_VER_ID,
        content="外来项目大纲",
    )

    # 1. Pydantic request-level defense
    with pytest.raises(ValidationError, match="cross-project outline reference"):
        InitialDraftRequest(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            approved_outline=foreign_outline,
            allowed_segments=_allowed_segments(),
        )

    # 2. Context assembly engine-level defense
    mock_request = SimpleNamespace(
        project_id=PROJECT_ID,
        approved_outline=foreign_outline,
        allowed_segments=_allowed_segments(),
        contexts=(),
    )
    with pytest.raises(ContextIsolationError, match="approved outline project mismatch"):
        assemble_writer_context(mock_request, profile)  # type: ignore[arg-type]


def test_context_isolation_rejects_cross_project_auxiliary_context() -> None:
    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")

    foreign_context = WriterContextSnapshot(
        document_id=UUID("70000000-0000-4000-8000-000000000001"),
        version_id=UUID("70000000-0000-4000-8000-000000000002"),
        project_id=OTHER_PROJECT_ID,  # Foreign project!
        kind=WriterContextKind.STYLE_GUIDE,
        content="外来世界观设定",
    )

    # 1. Pydantic request-level defense
    with pytest.raises(ValidationError, match="cross-project writer context"):
        InitialDraftRequest(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            approved_outline=_outline_ref(),
            allowed_segments=_allowed_segments(),
            contexts=[foreign_context],
        )

    # 2. Context assembly engine-level defense
    mock_request = SimpleNamespace(
        project_id=PROJECT_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
        contexts=(foreign_context,),
    )
    with pytest.raises(ContextIsolationError, match="project mismatch"):
        assemble_writer_context(mock_request, profile)  # type: ignore[arg-type]


def test_review_context_isolation_rejects_disallowed_kind_for_role() -> None:
    registry = ProfileRegistry()
    profile = registry.load("editor_agent", None)

    # Editor only allows style_guide and previous_chapter_summary; lore_boundary is disallowed
    disallowed_context = ReviewContextSnapshot(
        document_id=UUID("70000000-0000-4000-8000-000000000001"),
        version_id=UUID("70000000-0000-4000-8000-000000000002"),
        project_id=PROJECT_ID,
        kind=ReviewContextKind.LORE_BOUNDARY,
        content="核心世界观禁令",
    )

    # 1. Pydantic request-level defense
    with pytest.raises(ValidationError, match="review context is not allowed for this reviewer"):
        EditorReviewRequest(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            target=ChapterReviewTarget(
                project_id=PROJECT_ID,
                chapter_id=CHAPTER_ID,
                document_id=DRAFT_DOC_ID,
                version_id=DRAFT_VER_ID,
                segments=[
                    ReviewSegmentSnapshot(
                        segment_id=SEG_1_ID,
                        index=1,
                        title="潜入雾巷",
                        content="正文内容...",
                    )
                ],
            ),
            approved_outline=_outline_snapshot(),
            contexts=[disallowed_context],
        )

    # 2. Context assembly engine-level defense
    mock_request = SimpleNamespace(
        project_id=PROJECT_ID,
        approved_outline=_outline_snapshot(),
        target=ChapterReviewTarget(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DRAFT_DOC_ID,
            version_id=DRAFT_VER_ID,
            segments=[
                ReviewSegmentSnapshot(
                    segment_id=SEG_1_ID,
                    index=1,
                    title="潜入雾巷",
                    content="正文内容...",
                )
            ],
        ),
        _ALLOWED_CONTEXTS=EditorReviewRequest._ALLOWED_CONTEXTS,
        contexts=(disallowed_context,),
    )
    with pytest.raises(ContextIsolationError, match="not allowed for editor"):
        assemble_review_context(mock_request, profile, ReviewerRole.EDITOR)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Token Budgeting, Priority Ordering, and Non-Silent Drop
# ---------------------------------------------------------------------------


def test_budget_exceeded_on_oversized_mandatory_outline() -> None:
    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")

    # Tight profile where mandatory prompt + outline exceeds budget
    tight_profile = profile.model_copy(
        update={"context_policy": profile.context_policy.model_copy(update={"max_context_tokens": 100})}
    )
    request = InitialDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
    )

    with pytest.raises(ContextBudgetExceededError, match="mandatory constraints.*exceed profile budget"):
        assemble_writer_context(request, tight_profile)


def test_budget_prioritization_retains_high_priority_and_records_excluded() -> None:
    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")

    # High priority: style guide (~50 tokens)
    high_prio_ctx = WriterContextSnapshot(
        document_id=UUID("70000000-0000-4000-8000-000000000001"),
        version_id=UUID("70000000-0000-4000-8000-000000000002"),
        project_id=PROJECT_ID,
        kind=WriterContextKind.STYLE_GUIDE,
        content="核心风格指南：严肃克苏鲁悬疑。" * 5,
    )
    # Low priority: timeline with large content (~3000 tokens)
    low_prio_ctx = WriterContextSnapshot(
        document_id=UUID("80000000-0000-4000-8000-000000000001"),
        version_id=UUID("80000000-0000-4000-8000-000000000002"),
        project_id=PROJECT_ID,
        kind=WriterContextKind.TIMELINE,
        content="巨大时间线年表详细记录纪元..." * 500,
    )

    request = InitialDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=_outline_ref(),
        allowed_segments=_allowed_segments(),
        contexts=[low_prio_ctx, high_prio_ctx],  # unsorted input
    )

    # Set budget to allow invariant + high_prio_ctx, but not low_prio_ctx
    assembled_base = assemble_writer_context(request, profile)
    invariant_tokens = assembled_base.provenance.estimated_total_tokens - len(low_prio_ctx.content) // 2
    tight_budget = invariant_tokens + 200

    tight_profile = profile.model_copy(
        update={"context_policy": profile.context_policy.model_copy(update={"max_context_tokens": tight_budget})}
    )

    assembled = assemble_writer_context(request, tight_profile)
    prov = assembled.provenance

    # Higher priority (style guide) was preserved
    assert str(high_prio_ctx.document_id) in prov.included_context_document_ids
    # Low priority (timeline) was excluded because of budget constraints
    assert str(low_prio_ctx.document_id) in prov.excluded_context_document_ids
    # Provenance audit shows exact non-silent accounting
    assert len(prov.included_context_document_ids) + len(prov.excluded_context_document_ids) == 2


# ---------------------------------------------------------------------------
# 5. Execution and Verification of the 4 Quality Benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_quality_benchmarks_execution_and_criteria() -> None:
    """Verifies all 4 benchmark cases against human rubric expectations and automated review outcomes."""
    for case in ALL_QUALITY_BENCHMARKS:
        # Check human scores against rubric
        for dim, score in case.expected_human_scores.items():
            assert 1 <= score <= 5
            criteria = [c for c in HUMAN_EVALUATION_RUBRIC[dim] if c.score == score]
            assert len(criteria) == 1
            if case.flaw_type == BenchmarkFlawType.NONE_QUALIFIED:
                assert criteria[0].pass_threshold is True
            elif (
                (case.flaw_type == BenchmarkFlawType.MOTIVATION_BREAKDOWN and dim == EvaluationDimension.CHARACTER_MOTIVATION)
                or (case.flaw_type == BenchmarkFlawType.TIMELINE_LORE_CONFLICT and dim == EvaluationDimension.TIMELINE_LORE_CONSISTENCY)
                or (case.flaw_type == BenchmarkFlawType.STYLE_MISMATCH and dim == EvaluationDimension.STYLE_AND_TONE)
            ):
                assert criteria[0].pass_threshold is False

        # Build target segments
        target_segs = [
            ReviewSegmentSnapshot(
                segment_id=UUID(s["segment_id"]),
                index=s["index"],
                title=s["title"],
                content=s["content"],
            )
            for s in case.segments
        ]
        target = ChapterReviewTarget(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            document_id=case.target_document_id,
            version_id=case.target_version_id,
            segments=target_segs,
        )
        outline = ApprovedOutlineSnapshot(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            document_id=case.outline_document_id,
            version_id=case.outline_version_id,
            content=case.outline_content,
        )

        # 1. Editor Review
        editor_findings = []
        if case.flaw_type == BenchmarkFlawType.MOTIVATION_BREAKDOWN:
            editor_findings.append({
                "sequence": 1,
                "code": "editor_motivation_collapse",
                "severity": "blocking",
                "required": True,
                "evidence_segment_ids": [str(case.segment_two_id)],
                "rationale": "林野在无外部胁迫下突然下跪认罪，动机严重断裂。",
                "suggested_action": "重构第2段冲突，维持林野探案动机。",
            })
        elif case.flaw_type == BenchmarkFlawType.STYLE_MISMATCH:
            editor_findings.append({
                "sequence": 1,
                "code": "editor_fourth_wall_meme_break",
                "severity": "blocking",
                "required": True,
                "evidence_segment_ids": [str(case.segment_one_id), str(case.segment_two_id)],
                "rationale": "文中突兀插入现代网络梗与打破第四面墙吐槽。",
                "suggested_action": "清除所有网络用语，回归19世纪蒸汽冷峻语域。",
            })

        editor_provider = CaptureChapterReviewProvider(findings=editor_findings)
        editor_agent = EditorAgent(provider=editor_provider)
        editor_req = EditorReviewRequest(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=RUN_ID,
            target=target,
            approved_outline=outline,
            contexts=list(case.review_contexts.get(ReviewerRole.EDITOR, ())),
        )
        editor_report = await editor_agent.review(editor_req)
        assert isinstance(editor_report, ChapterReviewReport)

        if case.expected_review_outcome[ReviewerRole.EDITOR] == "blocking":
            assert editor_report.passed is False
            assert any(f.severity == ReviewFindingSeverity.BLOCKING for f in editor_report.findings)
        elif case.expected_review_outcome[ReviewerRole.EDITOR] == "passed":
            assert editor_report.passed is True

        # 2. Lore Review
        lore_findings = []
        if case.flaw_type == BenchmarkFlawType.TIMELINE_LORE_CONFLICT:
            lore_findings.append({
                "sequence": 1,
                "code": "lore_deceased_character_revived",
                "severity": "blocking",
                "required": True,
                "evidence_segment_ids": [str(case.segment_two_id)],
                "rationale": "日蚀纪元 95 年牺牲的大执政官在 100 年现身喝茶，严重推翻核心时间线。",
                "suggested_action": "删除死者登场，替换为导师旧信件或幻象残留。",
            })

        lore_provider = CaptureChapterReviewProvider(findings=lore_findings)
        lore_agent = LoreChapterFinalAgent(provider=lore_provider)
        lore_req = LoreChapterFinalRequest(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=RUN_ID,
            target=target,
            approved_outline=outline,
            contexts=list(case.review_contexts.get(ReviewerRole.LORE, ())),
        )
        lore_report = await lore_agent.review(lore_req)
        assert isinstance(lore_report, ChapterReviewReport)

        if case.expected_review_outcome[ReviewerRole.LORE] == "blocking":
            assert lore_report.passed is False
            assert any(f.severity == ReviewFindingSeverity.BLOCKING for f in lore_report.findings)
        elif case.expected_review_outcome[ReviewerRole.LORE] == "passed":
            assert lore_report.passed is True
