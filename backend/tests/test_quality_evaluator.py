"""Deterministic unit tests for quality benchmarks and evaluator engine (#266).

Validates:
1. 6 canonical quality benchmark cases completeness and contract conformance.
2. 8 standardized evaluation dimensions and 1-5 rubric scoring thresholds.
3. Reviewer report evaluation algorithms (evidence accuracy, false positive/negative detection).
4. Writer candidate evaluation algorithms (non-target preservation, style, lore).
5. Consolidated report compilation, markdown generation, and json serialization.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.agents.chapter_review_contracts import (
    ChapterReviewFinding,
    ChapterReviewReport,
    ReviewerRole,
    ReviewFindingSeverity,
)
from app.agents.chapter_writer_contracts import (
    CandidateChapterOutput,
    CandidateChapterSegment,
    CandidateSelfCheck,
)
from app.agents.quality_evaluator import (
    compile_report,
    evaluate_review_report,
    evaluate_writer_candidate,
    score_evidence_accuracy,
    score_expression_readability,
    score_false_positive_negative_rate,
    score_invocation_cost_efficiency,
    score_modification_scope_control,
    score_timeline_lore_consistency,
    score_writer_style_and_tone,
)
from tests.quality_benchmarks import (
    ALL_QUALITY_BENCHMARKS,
    BenchmarkFlawType,
    CASE_INSUFFICIENT_MATERIALS,
    CASE_MOTIVATION_BREAKDOWN,
    CASE_NON_TARGET_PRESERVATION,
    CASE_QUALIFIED_CHAPTER,
    CASE_STYLE_MISMATCH,
    CASE_TIMELINE_LORE_CONFLICT,
    EvaluationDimension,
    HUMAN_EVALUATION_RUBRIC,
    QualityBenchmarkCase,
    get_benchmark_by_id,
)


class TestBenchmarkCasesCompleteness:
    """Validate completeness and schema contracts for all 6 benchmark cases."""

    def test_canonical_cases_count_and_types(self) -> None:
        assert len(ALL_QUALITY_BENCHMARKS) == 6

        flaw_types = {case.flaw_type for case in ALL_QUALITY_BENCHMARKS}
        assert flaw_types == {
            BenchmarkFlawType.MOTIVATION_BREAKDOWN,
            BenchmarkFlawType.TIMELINE_LORE_CONFLICT,
            BenchmarkFlawType.STYLE_MISMATCH,
            BenchmarkFlawType.NONE_QUALIFIED,
            BenchmarkFlawType.NON_TARGET_PRESERVATION,
            BenchmarkFlawType.INSUFFICIENT_MATERIALS,
        }

    def test_benchmark_lookup_by_id(self) -> None:
        case = get_benchmark_by_id("bench-non-target-preservation-05")
        assert case.case_id == "bench-non-target-preservation-05"
        assert case.flaw_type is BenchmarkFlawType.NON_TARGET_PRESERVATION

        with pytest.raises(KeyError):
            get_benchmark_by_id("non-existent-case-id")

    @pytest.mark.parametrize("case", ALL_QUALITY_BENCHMARKS)
    def test_case_segments_structure(self, case: QualityBenchmarkCase) -> None:
        assert len(case.segments) == 2
        for seg in case.segments:
            assert "segment_id" in seg
            assert "index" in seg
            assert "title" in seg
            assert "content" in seg
            assert len(seg["content"]) >= 20

    @pytest.mark.parametrize("case", ALL_QUALITY_BENCHMARKS)
    def test_case_contexts_and_expectations(self, case: QualityBenchmarkCase) -> None:
        assert len(case.writer_contexts) >= 1
        assert ReviewerRole.EDITOR in case.review_contexts
        assert ReviewerRole.CHIEF_EDITOR in case.review_contexts
        assert ReviewerRole.LORE in case.review_contexts

        for role in (ReviewerRole.EDITOR, ReviewerRole.CHIEF_EDITOR, ReviewerRole.LORE):
            assert role in case.expected_review_outcome
            assert case.expected_review_outcome[role] in ("passed", "warning", "blocking")


class TestEvaluationDimensionsAndRubric:
    """Validate 8 canonical evaluation dimensions and 1-5 rubrics."""

    def test_all_8_dimensions_in_rubric(self) -> None:
        canonical_8 = [
            EvaluationDimension.REQUIREMENT_ADHERENCE,
            EvaluationDimension.TIMELINE_LORE_CONSISTENCY,
            EvaluationDimension.STYLE_AND_TONE,
            EvaluationDimension.EXPRESSION_READABILITY,
            EvaluationDimension.EVIDENCE_ACCURACY,
            EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
            EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
            EvaluationDimension.INVOCATION_COST_EFFICIENCY,
        ]
        for dim in canonical_8:
            assert dim in HUMAN_EVALUATION_RUBRIC
            criteria = HUMAN_EVALUATION_RUBRIC[dim]
            assert len(criteria) == 5
            scores = [c.score for c in criteria]
            assert scores == [5, 4, 3, 2, 1]

            # Scores 5, 4, 3 are passing; 2, 1 are failing
            for c in criteria:
                if c.score >= 3:
                    assert c.pass_threshold is True
                else:
                    assert c.pass_threshold is False


class TestReviewReportEvaluationAlgorithms:
    """Validate evaluation algorithms for review reports."""

    def test_evidence_accuracy_with_valid_citations(self) -> None:
        case = CASE_MOTIVATION_BREAKDOWN
        valid_seg_id = UUID(case.segments[0]["segment_id"])

        finding = ChapterReviewFinding(
            sequence=1,
            code="editor_motivation_collapse",
            severity=ReviewFindingSeverity.BLOCKING,
            required=True,
            evidence_segment_ids=(valid_seg_id,),
            rationale="侦探在没有任何压力的情况下突兀自首。",
            suggested_action="恢复林野审慎隐忍的自卫反击逻辑。",
        )
        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=False,
            summary="发现严重人物动机断裂缺陷。",
            findings=(finding,),
        )

        score = score_evidence_accuracy(report, case)
        assert score.score == 5
        assert score.pass_threshold is True
        assert "全部 1 处引用段落均真实存在" in score.rationale

    def test_evidence_accuracy_with_fabricated_citations(self) -> None:
        case = CASE_MOTIVATION_BREAKDOWN
        fake_seg_id = uuid4()

        finding = ChapterReviewFinding(
            sequence=1,
            code="editor_motivation_collapse",
            severity=ReviewFindingSeverity.BLOCKING,
            required=True,
            evidence_segment_ids=(fake_seg_id,),
            rationale="虚构引用证据测试。",
            suggested_action="修正引用。",
        )
        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=False,
            summary="发现严重人物动机断裂缺陷。",
            findings=(finding,),
        )

        score = score_evidence_accuracy(report, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "凭空捏造证据" in score.rationale

    def test_false_positive_on_qualified_chapter(self) -> None:
        case = CASE_QUALIFIED_CHAPTER
        valid_seg_id = UUID(case.segments[0]["segment_id"])

        # Reviewer blocks a qualified chapter -> False Positive
        finding = ChapterReviewFinding(
            sequence=1,
            code="editor_false_alarm",
            severity=ReviewFindingSeverity.BLOCKING,
            required=True,
            evidence_segment_ids=(valid_seg_id,),
            rationale="错误判定合格文本存在阻断硬伤。",
            suggested_action="无。",
        )
        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=False,
            summary="误判拦截。",
            findings=(finding,),
        )

        score = score_false_positive_negative_rate(report, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "严重误报" in score.rationale

    def test_zero_false_alarm_on_qualified_chapter(self) -> None:
        case = CASE_QUALIFIED_CHAPTER

        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=True,
            summary="全章行文严谨自洽，符合出版级标准，予以完全通过。",
            findings=(),
        )

        score = score_false_positive_negative_rate(report, case)
        assert score.score == 5
        assert score.pass_threshold is True
        assert "零误报" in score.rationale

    def test_false_negative_on_flawed_chapter(self) -> None:
        case = CASE_MOTIVATION_BREAKDOWN

        # Reviewer passes a severely flawed chapter -> False Negative
        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=True,
            summary="漏检放行。",
            findings=(),
        )

        score = score_false_positive_negative_rate(report, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "严重漏报" in score.rationale


class TestWriterCandidateEvaluationAlgorithms:
    """Validate evaluation algorithms for writer outputs and non-target preservation."""

    def test_non_target_preservation_perfect_match(self) -> None:
        case = CASE_NON_TARGET_PRESERVATION
        original_seg2_content = case.segments[1]["content"]

        candidate = CandidateChapterOutput(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            approved_outline_document_id=case.outline_document_id,
            approved_outline_version_id=case.outline_version_id,
            complete_chapter=True,
            segments=(
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[0]["segment_id"]),
                    index=1,
                    title="修订后的开篇",
                    content="修订后的段落1文本，解决了节奏拖沓问题。",
                ),
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[1]["segment_id"]),
                    index=2,
                    title=case.segments[1]["title"],
                    content=original_seg2_content,  # 100% exact character-level preservation
                ),
            ),
            summary="对段落1实施了手术刀式节奏调整，段落2完整保留。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )

        score = score_modification_scope_control(candidate, case)
        assert score.score == 5
        assert score.pass_threshold is True
        assert "手术刀级精准保留" in score.rationale

    def test_non_target_preservation_violation(self) -> None:
        case = CASE_NON_TARGET_PRESERVATION

        candidate = CandidateChapterOutput(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            approved_outline_document_id=case.outline_document_id,
            approved_outline_version_id=case.outline_version_id,
            complete_chapter=True,
            segments=(
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[0]["segment_id"]),
                    index=1,
                    title="修订后的开篇",
                    content="修改段落1。",
                ),
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[1]["segment_id"]),
                    index=2,
                    title=case.segments[1]["title"],
                    content="非目标段落被大改重写，完全改变了原样和伏笔。",
                ),
            ),
            summary="越界重写了段落2。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )

        score = score_modification_scope_control(candidate, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "严重越界修改" in score.rationale

    def test_style_and_tone_forbidden_memes(self) -> None:
        case = CASE_STYLE_MISMATCH

        candidate = CandidateChapterOutput(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            approved_outline_document_id=case.outline_document_id,
            approved_outline_version_id=case.outline_version_id,
            complete_chapter=True,
            segments=(
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[0]["segment_id"]),
                    index=1,
                    title="违规网络用语段落",
                    content="林野大喊一声老铁666，系统已激活主角光环！",
                ),
            ),
            summary="包含网络流行语。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )

        score = score_writer_style_and_tone(candidate, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "风格坍塌" in score.rationale

    def test_timeline_lore_breach_detection(self) -> None:
        case = CASE_TIMELINE_LORE_CONFLICT

        candidate = CandidateChapterOutput(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            approved_outline_document_id=case.outline_document_id,
            approved_outline_version_id=case.outline_version_id,
            complete_chapter=True,
            segments=(
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[0]["segment_id"]),
                    index=1,
                    title="死者复生硬伤段落",
                    content="凡恩大执政官端起红茶微笑着走过来，推开了黄铜封印之门。",
                ),
            ),
            summary="复活已死角色。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )

        score = score_timeline_lore_consistency(candidate, case)
        assert score.score == 1
        assert score.pass_threshold is False
        assert "严重世界观硬伤" in score.rationale

    def test_token_cost_efficiency_scoring(self) -> None:
        # Bounded token usage
        score_good = score_invocation_cost_efficiency(
            {"input_tokens": 500, "output_tokens": 1200, "total_tokens": 1700},
            latency_ms=8500.0,
        )
        assert score_good.score == 5
        assert score_good.pass_threshold is True

        # Runaway token usage
        score_bad = score_invocation_cost_efficiency(
            {"input_tokens": 5000, "output_tokens": 12000, "total_tokens": 17000},
            latency_ms=95000.0,
        )
        assert score_bad.score == 1
        assert score_bad.pass_threshold is False

    def test_evaluate_writer_candidate_full(self) -> None:
        case = CASE_NON_TARGET_PRESERVATION
        candidate = CandidateChapterOutput(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            approved_outline_document_id=case.outline_document_id,
            approved_outline_version_id=case.outline_version_id,
            complete_chapter=True,
            segments=(
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[0]["segment_id"]),
                    index=1,
                    title="新开篇",
                    content="冷雨敲打着煤气灯罩，黄铜齿轮在雾气深处咔嗒转动。林野压低帽檐，目光穿透雨幕。",
                ),
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[1]["segment_id"]),
                    index=2,
                    title=case.segments[1]["title"],
                    content=case.segments[1]["content"],
                ),
            ),
            summary="手术刀微调段落1，段落2保持字符级一致。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )

        result = evaluate_writer_candidate(case, candidate, latency_ms=1200.0)
        assert result.passed is True
        assert result.role == "writer"
        assert result.dimension_scores[EvaluationDimension.MODIFICATION_SCOPE_CONTROL.value].score == 5
        assert result.average_score >= 4.5

    def test_expression_readability_scoring(self) -> None:
        # Repetitive loop text
        repetitive_text = (
            "林野停下了脚步。林野停下了脚步。林野停下了脚步。林野停下了脚步。林野停下了脚步。"
        )
        score_rep = score_expression_readability(repetitive_text, label="循环文本")
        assert score_rep.score == 2
        assert score_rep.pass_threshold is False

    def test_insufficient_materials_warning_calibration(self) -> None:
        case = CASE_INSUFFICIENT_MATERIALS
        seg_id = UUID(case.segments[0]["segment_id"])

        # Reviewer issues warning without blocking -> Should score 5
        finding = ChapterReviewFinding(
            sequence=1,
            code="lore_setting_gap",
            severity=ReviewFindingSeverity.WARNING,
            required=False,
            evidence_segment_ids=(seg_id,),
            rationale="星盘残件材料非已知设定，建议后续在世界观设定集中增补该古代器物说明。",
            suggested_action="增补世界观档案。",
        )
        report = ChapterReviewReport(
            project_id=case.project_id,
            chapter_id=case.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case.target_document_id,
            target_version_id=case.target_version_id,
            passed=True,
            summary="材料不足场景下提示完善设定，整体行文合格。",
            findings=(finding,),
        )

        score = score_false_positive_negative_rate(report, case)
        assert score.score == 5
        assert score.pass_threshold is True
        assert "对背景材料缺失保持克制与包容" in score.rationale


class TestReportCompilationAndExport:
    """Validate report aggregation, Markdown rendering, and JSON serialization."""

    def test_full_evaluation_and_report_compilation(self, tmp_path) -> None:
        case_qual = CASE_QUALIFIED_CHAPTER
        report_qual = ChapterReviewReport(
            project_id=case_qual.project_id,
            chapter_id=case_qual.chapter_id,
            workflow_run_id=uuid4(),
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
            target_document_id=case_qual.target_document_id,
            target_version_id=case_qual.target_version_id,
            passed=True,
            summary="高质量合格章节，叙事节奏与氛围感极佳。",
            findings=(),
        )

        res_qual = evaluate_review_report(case_qual, report_qual)
        assert res_qual.passed is True
        assert res_qual.average_score >= 4.5

        # Compile report
        report = compile_report([res_qual], model="mock-test-model")
        assert report.total_cases == 1
        assert report.passed_cases == 1
        assert report.failed_cases == 0
        assert report.overall_score >= 4.0

        # Markdown output verification
        md = report.to_markdown()
        assert "# GuraNovel Agent Quality Evaluation Report" in md
        assert "bench-qualified-chapter-04" in md
        assert "PASSED" in md
        assert "| `evidence_accuracy` |" in md

        # JSON output verification
        data = report.to_dict()
        assert data["total_cases"] == 1
        assert data["model"] == "mock-test-model"
        assert len(data["cases"]) == 1

        # File persistence test
        md_file = tmp_path / "test_report.md"
        report.save(md_file)
        assert md_file.exists()
        assert "GuraNovel" in md_file.read_text(encoding="utf-8")

        json_file = tmp_path / "test_report.json"
        report.save(json_file)
        assert json_file.exists()
        loaded = json.loads(json_file.read_text(encoding="utf-8"))
        assert loaded["report_id"] == report.report_id
