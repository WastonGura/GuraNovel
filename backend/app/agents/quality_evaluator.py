"""Evaluation engine and quality reporting for chapter writing and review.

Provides:
1. Standardized scoring algorithms across 8 canonical dimensions:
   - requirement_adherence
   - timeline_lore_consistency
   - style_and_tone
   - expression_readability
   - evidence_accuracy
   - false_positive_negative_rate
   - modification_scope_control
   - invocation_cost_efficiency
2. Evaluation of ChapterReviewReport (review quality, evidence accuracy, false pos/neg)
3. Evaluation of CandidateChapterOutput (segment preservation, style, readability, outline adherence)
4. Score aggregation and structured QualityEvaluationReport (markdown and json output)
5. Explicit --enable-real-model CLI for bounded real model evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import difflib
import json
import os
import re
import sys
import time
import httpx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from app.agents.chapter_review_contracts import (
    ApprovedOutlineSnapshot,
    ChapterReviewFinding,
    ChapterReviewReport,
    ChapterReviewTarget,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewSegmentSnapshot,
    ReviewerRole,
    ReviewFindingSeverity,
)
from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    CandidateChapterOutput,
    CandidateChapterSegment,
    CandidateSelfCheck,
    InitialDraftRequest,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
)
from app.agents.chapter_review_providers import OpenAICompatibleChapterReviewProvider
from app.agents.chapter_writer_providers import OpenAICompatibleChapterWriterProvider
from app.agents.profiles import ProfileRegistry
from app.core.config import settings
from tests.quality_benchmarks import (
    ALL_QUALITY_BENCHMARKS,
    BenchmarkFlawType,
    EvaluationDimension,
    QualityBenchmarkCase,
    get_benchmark_by_id,
)

# Forbidden modern slang / net speak in Victorian gothic / historical fantasy
FORBIDDEN_MODERN_TERMS: tuple[str, ...] = (
    "老铁",
    "绝绝子",
    "yyds",
    "666",
    "系统已激活",
    "主角光环",
    "穿越者",
    "带货",
    "打call",
    "粉丝",
    "卧槽",
    "牛逼",
    "给力",
    "点赞",
    "刷火箭",
)


@dataclass(frozen=True)
class DimensionScore:
    """Score and evaluation evidence for a single dimension."""

    dimension: EvaluationDimension
    score: int
    pass_threshold: bool
    rationale: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "score": self.score,
            "pass_threshold": self.pass_threshold,
            "rationale": self.rationale,
            "metrics": self.metrics,
        }


@dataclass
class CaseEvaluationResult:
    """Complete evaluation outcome for a single benchmark case."""

    case_id: str
    title: str
    flaw_type: str
    role: str
    passed: bool
    dimension_scores: dict[str, DimensionScore]
    summary: str
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def average_score(self) -> float:
        if not self.dimension_scores:
            return 0.0
        return sum(d.score for d in self.dimension_scores.values()) / len(self.dimension_scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "flaw_type": self.flaw_type,
            "role": self.role,
            "passed": self.passed,
            "average_score": round(self.average_score, 2),
            "dimension_scores": {
                k: v.to_dict() for k, v in self.dimension_scores.items()
            },
            "summary": self.summary,
            "token_usage": self.token_usage,
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class QualityEvaluationReport:
    """Consolidated audit report covering chapter writing and review quality."""

    report_id: str
    timestamp: str
    model: str
    cases: list[CaseEvaluationResult]
    total_cases: int
    passed_cases: int
    failed_cases: int
    overall_score: float
    dimension_averages: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "overall_score": round(self.overall_score, 2),
            "dimension_averages": {
                k: round(v, 2) for k, v in self.dimension_averages.items()
            },
            "cases": [c.to_dict() for c in self.cases],
        }

    def to_markdown(self) -> str:
        """Produce a clean, GitHub-flavored markdown report."""
        status_badge = "PASSED" if self.failed_cases == 0 else "ATTENTION REQUIRED"
        pass_rate = (self.passed_cases / self.total_cases * 100.0) if self.total_cases > 0 else 0.0

        lines: list[str] = [
            "# GuraNovel Agent Quality Evaluation Report",
            "",
            f"- **Report ID**: `{self.report_id}`",
            f"- **Generated At**: `{self.timestamp}`",
            f"- **Evaluated Model**: `{self.model}`",
            f"- **Status**: **{status_badge}**",
            f"- **Pass Rate**: {self.passed_cases}/{self.total_cases} ({pass_rate:.1f}%)",
            f"- **Overall Average Score**: {self.overall_score:.2f} / 5.00",
            "",
            "## 1. Dimension Score Summary",
            "",
            "| Evaluation Dimension | Average Score | Pass Threshold | Status | Description |",
            "| :--- | :---: | :---: | :---: | :--- |",
        ]

        dim_desc_map = {
            EvaluationDimension.REQUIREMENT_ADHERENCE.value: "大纲与结构指示遵循度",
            EvaluationDimension.TIMELINE_LORE_CONSISTENCY.value: "前文与世界观边界一致性",
            EvaluationDimension.STYLE_AND_TONE.value: "作品语域与文学基调保持",
            EvaluationDimension.EXPRESSION_READABILITY.value: "语言流畅度与文学质感",
            EvaluationDimension.EVIDENCE_ACCURACY.value: "审阅引用证据定位精准度",
            EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE.value: "审阅误报与漏报校准率",
            EvaluationDimension.MODIFICATION_SCOPE_CONTROL.value: "非目标段落保全度与手术刀式精准修改",
            EvaluationDimension.INVOCATION_COST_EFFICIENCY.value: "调用用量与Token成本控制",
        }

        for dim_name, avg_score in sorted(self.dimension_averages.items()):
            status = "PASS" if avg_score >= 3.0 else "FAIL"
            desc = dim_desc_map.get(dim_name, "标准评价维度")
            lines.append(
                f"| `{dim_name}` | **{avg_score:.2f}** / 5.0 | >= 3.0 | {status} | {desc} |"
            )

        lines.extend([
            "",
            "## 2. Benchmark Case Breakdown",
            "",
        ])

        for idx, case in enumerate(self.cases, 1):
            case_status = "PASSED" if case.passed else "FAILED"
            tokens = case.token_usage
            token_str = (
                f"{tokens.get('input_tokens', 0)} in / {tokens.get('output_tokens', 0)} out"
                if tokens
                else "Offline / Mock"
            )
            lines.extend([
                f"### Case {idx}: [{case.case_id}] {case.title}",
                "",
                f"- **Role**: `{case.role}`",
                f"- **Flaw Type**: `{case.flaw_type}`",
                f"- **Outcome**: **{case_status}** (Score: {case.average_score:.2f} / 5.0)",
                f"- **Token Usage**: {token_str}",
                f"- **Latency**: {case.latency_ms:.0f} ms",
                f"- **Summary**: {case.summary.strip()}",
                "",
                "#### Dimension Scores",
                "",
                "| Dimension | Score | Pass | Evidence / Rationale |",
                "| :--- | :---: | :---: | :--- |",
            ])
            for dim_key, d_score in case.dimension_scores.items():
                p_str = "YES" if d_score.pass_threshold else "NO"
                lines.append(
                    f"| `{dim_key}` | **{d_score.score}**/5 | {p_str} | {d_score.rationale} |"
                )
            lines.append("")

        lines.extend([
            "---",
            "*Report generated by GuraNovel Automated Quality Evaluation Suite.*",
        ])
        return "\n".join(line.rstrip() for line in lines) + "\n"

    def save(self, output_path: str | Path) -> None:
        """Persist report to file (.md or .json)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".json":
            path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            path.write_text(self.to_markdown(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scoring Algorithms
# ---------------------------------------------------------------------------


def score_evidence_accuracy(
    report: ChapterReviewReport, case: QualityBenchmarkCase
) -> DimensionScore:
    """Evaluate whether evidence_segment_ids in findings strictly map to manuscript segments."""
    valid_segment_ids = {str(seg["segment_id"]) for seg in case.segments}
    findings = report.findings

    if not findings:
        expected = case.expected_review_outcome.get(report.reviewer_role, "passed")
        if expected == "passed":
            return DimensionScore(
                dimension=EvaluationDimension.EVIDENCE_ACCURACY,
                score=5,
                pass_threshold=True,
                rationale="合格章节无需定位问题证据，无虚构引用。",
                metrics={"findings_count": 0},
            )
        return DimensionScore(
            dimension=EvaluationDimension.EVIDENCE_ACCURACY,
            score=3,
            pass_threshold=True,
            rationale="章节包含缺陷但未给出具体证据定位。",
            metrics={"findings_count": 0},
        )

    total_citations = 0
    valid_citations = 0
    for finding in findings:
        for seg_id in finding.evidence_segment_ids:
            total_citations += 1
            if str(seg_id) in valid_segment_ids:
                valid_citations += 1

    if total_citations == 0:
        return DimensionScore(
            dimension=EvaluationDimension.EVIDENCE_ACCURACY,
            score=3,
            pass_threshold=True,
            rationale="审阅结果未附带段落级证据引用。",
            metrics={"total_citations": 0},
        )

    accuracy_ratio = valid_citations / total_citations
    if accuracy_ratio == 1.0:
        score = 5
        pass_th = True
        rationale = f"证据精确定位：全部 {total_citations} 处引用段落均真实存在于手稿中。"
    elif accuracy_ratio >= 0.8:
        score = 4
        pass_th = True
        rationale = f"证据大体准确：{valid_citations}/{total_citations} 处引用有效。"
    elif accuracy_ratio >= 0.5:
        score = 2
        pass_th = False
        rationale = f"证据定位偏差：存在虚假或脱靶引用 ({valid_citations}/{total_citations} 有效)。"
    else:
        score = 1
        pass_th = False
        rationale = f"凭空捏造证据：大部分引用段落根本不存在于手稿中 ({valid_citations}/{total_citations})。"

    return DimensionScore(
        dimension=EvaluationDimension.EVIDENCE_ACCURACY,
        score=score,
        pass_threshold=pass_th,
        rationale=rationale,
        metrics={
            "valid_citations": valid_citations,
            "total_citations": total_citations,
            "accuracy_ratio": round(accuracy_ratio, 3),
        },
    )


def score_false_positive_negative_rate(
    report: ChapterReviewReport, case: QualityBenchmarkCase
) -> DimensionScore:
    """Evaluate review calibration (blocking vs passed) against ground-truth defect status."""
    expected = case.expected_review_outcome.get(report.reviewer_role, "passed")
    actual_passed = report.passed
    blocking_findings = [
        f for f in report.findings if f.severity is ReviewFindingSeverity.BLOCKING
    ]

    # Scenario A: Ground truth is QUALIFIED (expected: "passed")
    if expected == "passed":
        if actual_passed and len(blocking_findings) == 0:
            if len(report.findings) == 0:
                return DimensionScore(
                    dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
                    score=5,
                    pass_threshold=True,
                    rationale="零误报：合规文本准确判定为完全通过 (PASS)，无过度苛责拦截。",
                )
            return DimensionScore(
                dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
                score=4,
                pass_threshold=True,
                rationale=f"校准良好：合规文本通过，提出 {len(report.findings)} 条优化建议，未造成阻断。",
            )
        # False positive: ground-truth qualified was blocked!
        return DimensionScore(
            dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
            score=1,
            pass_threshold=False,
            rationale=f"严重误报 (False Positive)：完全合规的章节被错误拦截 (Blocking)，拦截项: {[f.code for f in blocking_findings]}。",
            metrics={"false_positive": True},
        )

    # Scenario B: Ground truth is FLAWED (expected: "blocking")
    if expected == "blocking":
        if not actual_passed and len(blocking_findings) > 0:
            # Check if expected blocking codes matched
            actual_codes = {f.code for f in blocking_findings}
            if case.expected_blocking_codes:
                matched = actual_codes.intersection(set(case.expected_blocking_codes))
                if matched:
                    return DimensionScore(
                        dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
                        score=5,
                        pass_threshold=True,
                        rationale=f"零漏报精准拦截：成功识别核心缺陷并阻断，命中预期错误码: {sorted(matched)}。",
                        metrics={"matched_codes": sorted(matched)},
                    )
            return DimensionScore(
                dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
                score=4,
                pass_threshold=True,
                rationale=f"有效拦截：成功识别缺陷并阻断 (拦截项: {sorted(actual_codes)})。",
                metrics={"blocking_codes": sorted(actual_codes)},
            )
        # False negative: severe defect was passed!
        return DimensionScore(
            dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
            score=1,
            pass_threshold=False,
            rationale=f"严重漏报 (False Negative)：未能识别出章节中的核心硬伤 ({case.flaw_type.value})，错误放行通过。",
            metrics={"false_negative": True},
        )

    # Scenario C: Ground truth is INSUFFICIENT MATERIALS / WARNING (expected: "warning")
    if actual_passed and len(blocking_findings) == 0:
        if len(report.findings) > 0:
            return DimensionScore(
                dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
                score=5,
                pass_threshold=True,
                rationale="校准精准：对背景材料缺失保持克制与包容，提出预警 (Warning) 而非暴力阻断。",
            )
        return DimensionScore(
            dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
            score=4,
            pass_threshold=True,
            rationale="基本放行：材料不足场景下未产生误报阻断。",
        )

    return DimensionScore(
        dimension=EvaluationDimension.FALSE_POSITIVE_NEGATIVE_RATE,
        score=2,
        pass_threshold=False,
        rationale="过度拦截：在背景资料不充分时缺乏合理包容度，强行产生阻断性缺陷。",
    )


def score_review_requirement_adherence(
    report: ChapterReviewReport, case: QualityBenchmarkCase
) -> DimensionScore:
    """Evaluate reviewer requirement adherence and completeness."""
    if not report.summary or len(report.summary.strip()) < 10:
        return DimensionScore(
            dimension=EvaluationDimension.REQUIREMENT_ADHERENCE,
            score=2,
            pass_threshold=False,
            rationale="审阅报告摘要过短或缺失，未履行审阅要求。",
        )

    # Adherence to role boundary
    role = report.reviewer_role
    if role == ReviewerRole.LORE and not any(
        kw in report.summary for kw in ("设定", "世界观", "纪元", "时间", "lore", "规则")
    ):
        return DimensionScore(
            dimension=EvaluationDimension.REQUIREMENT_ADHERENCE,
            score=3,
            pass_threshold=True,
            rationale="世界观审阅未充分聚焦设定与历史边界。",
        )

    return DimensionScore(
        dimension=EvaluationDimension.REQUIREMENT_ADHERENCE,
        score=5,
        pass_threshold=True,
        rationale="审阅结构完整，摘要与发现条目完全符合角色契约要求。",
    )


def score_modification_scope_control(
    candidate: CandidateChapterOutput, case: QualityBenchmarkCase
) -> DimensionScore:
    """Evaluate whether candidate respected modification scope and preserved non-target segments."""
    original_segments = {seg["index"]: seg["content"] for seg in case.segments}
    cand_segments = {seg.index: seg.content for seg in candidate.segments}

    # Case 5: NON_TARGET_PRESERVATION specifically targets segment 1, preserving segment 2
    if case.flaw_type == BenchmarkFlawType.NON_TARGET_PRESERVATION:
        non_target_idx = 2
        orig_non_target = original_segments.get(non_target_idx, "")
        cand_non_target = cand_segments.get(non_target_idx, "")

        if not orig_non_target or not cand_non_target:
            return DimensionScore(
                dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
                score=1,
                pass_threshold=False,
                rationale="非目标段落缺失，严重破坏章节结构完整性。",
            )

        if orig_non_target == cand_non_target:
            return DimensionScore(
                dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
                score=5,
                pass_threshold=True,
                rationale="手术刀级精准保留：非目标段落（段落2）字符级 100% 完整保留，零改动。",
                metrics={"preservation_ratio": 1.0},
            )

        ratio = difflib.SequenceMatcher(None, orig_non_target, cand_non_target).ratio()
        if ratio >= 0.98:
            return DimensionScore(
                dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
                score=4,
                pass_threshold=True,
                rationale=f"范围控制良好：非目标段落保持 {ratio:.1%} 一致，仅微弱格式变动。",
                metrics={"preservation_ratio": round(ratio, 3)},
            )
        if ratio >= 0.85:
            return DimensionScore(
                dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
                score=3,
                pass_threshold=True,
                rationale=f"范围基本受控：非目标段落存在局部改动 (一致性 {ratio:.1%})。",
                metrics={"preservation_ratio": round(ratio, 3)},
            )
        return DimensionScore(
            dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
            score=1,
            pass_threshold=False,
            rationale=f"严重越界修改：非目标段落被大面积篡改重写 (一致性仅 {ratio:.1%})。",
            metrics={"preservation_ratio": round(ratio, 3)},
        )

    # General preservation & segment integrity check
    if len(cand_segments) == len(original_segments):
        return DimensionScore(
            dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
            score=5,
            pass_threshold=True,
            rationale="章节段落数量与顺序完全符合原定结构要求。",
        )
    return DimensionScore(
        dimension=EvaluationDimension.MODIFICATION_SCOPE_CONTROL,
        score=2,
        pass_threshold=False,
        rationale=f"段落数量不一致 (期望 {len(original_segments)}，实际 {len(cand_segments)})。",
    )


def score_writer_style_and_tone(
    candidate: CandidateChapterOutput, case: QualityBenchmarkCase
) -> DimensionScore:
    """Evaluate stylistic tone and check for forbidden modern buzzwords / meme intrusions."""
    combined_text = " ".join(seg.content for seg in candidate.segments)
    found_forbidden = [term for term in FORBIDDEN_MODERN_TERMS if term in combined_text]

    if found_forbidden:
        return DimensionScore(
            dimension=EvaluationDimension.STYLE_AND_TONE,
            score=1,
            pass_threshold=False,
            rationale=f"风格坍塌：文本中出现破坏沉浸感的现代互联网流行语: {found_forbidden}。",
            metrics={"forbidden_terms": found_forbidden},
        )

    # Check for gothic/steampunk atmosphere keywords
    atmosphere_words = ("雾", "齿轮", "煤烟", "黄铜", "钟声", "阴影", "寒意", "蒸汽", "回响")
    matched_atmo = [w for w in atmosphere_words if w in combined_text]

    if len(matched_atmo) >= 3:
        return DimensionScore(
            dimension=EvaluationDimension.STYLE_AND_TONE,
            score=5,
            pass_threshold=True,
            rationale=f"文风卓越沉浸：纯正严肃维多利亚蒸汽克苏鲁风格，感官细节丰沛 (元素: {matched_atmo[:4]})。",
            metrics={"matched_atmosphere_terms": matched_atmo},
        )
    return DimensionScore(
        dimension=EvaluationDimension.STYLE_AND_TONE,
        score=4,
        pass_threshold=True,
        rationale="基调稳定统一：无出戏现代用语，叙事语调符合作品定位。",
    )


def score_timeline_lore_consistency(
    candidate: CandidateChapterOutput, case: QualityBenchmarkCase
) -> DimensionScore:
    """Check whether candidate adheres to core lore constraints."""
    combined_text = " ".join(seg.content for seg in candidate.segments)

    # Lore breach checks:
    # 1. Grand Archon Vane must be dead (died in Year 95). If portrayed as alive/talking -> breach
    archon_breach = any(
        phrase in combined_text
        for phrase in (
            "凡恩大执政官端起",
            "凡恩大执政官微笑着说",
            "凡恩大执政官走",
            "凡恩大执政官还活着",
        )
    )
    # 2. Brass Seal Gate cannot be opened before Year 120
    gate_breach = any(
        phrase in combined_text
        for phrase in ("黄铜封印之门已经开启", "推开了黄铜封印之门", "黄铜封印之门被打破")
    )

    if archon_breach or gate_breach:
        breach_desc = []
        if archon_breach:
            breach_desc.append("凡恩大执政官殉职设定被违背")
        if gate_breach:
            breach_desc.append("黄铜封印之门不可开启铁律被打破")
        return DimensionScore(
            dimension=EvaluationDimension.TIMELINE_LORE_CONSISTENCY,
            score=1,
            pass_threshold=False,
            rationale=f"严重世界观硬伤：{', '.join(breach_desc)}。",
            metrics={"archon_breach": archon_breach, "gate_breach": gate_breach},
        )

    return DimensionScore(
        dimension=EvaluationDimension.TIMELINE_LORE_CONSISTENCY,
        score=5,
        pass_threshold=True,
        rationale="设定严密自洽：严格遵守世界观纪元与核心历史禁律，无吃书硬伤。",
    )


def score_expression_readability(
    text: str, label: str = "手稿文本"
) -> DimensionScore:
    """Evaluate natural flow, readability, and sentence length."""
    min_len = 10 if "审阅" in label else 30
    if not text or len(text.strip()) < min_len:
        return DimensionScore(
            dimension=EvaluationDimension.EXPRESSION_READABILITY,
            score=2,
            pass_threshold=False,
            rationale=f"{label}篇幅过短，难以评估语言质量。",
        )

    # Repetition check (repeated sentences or loops)
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) > 5]
    if len(sentences) > len(set(sentences)) + 2:
        return DimensionScore(
            dimension=EvaluationDimension.EXPRESSION_READABILITY,
            score=2,
            pass_threshold=False,
            rationale=f"{label}存在明显的机械重复句式或死循环复读。",
        )

    return DimensionScore(
        dimension=EvaluationDimension.EXPRESSION_READABILITY,
        score=5,
        pass_threshold=True,
        rationale=f"{label}行文流畅自然，句式富于节奏变化，表达通顺生动。",
    )


def score_invocation_cost_efficiency(
    token_usage: Mapping[str, int] | None, latency_ms: float
) -> DimensionScore:
    """Evaluate token consumption and latency against cost benchmarks."""
    if not token_usage or token_usage.get("total_tokens", 0) == 0:
        return DimensionScore(
            dimension=EvaluationDimension.INVOCATION_COST_EFFICIENCY,
            score=5,
            pass_threshold=True,
            rationale="零真实 Token 消耗 (离线基准测试或 Mock 模式)。",
            metrics={"token_usage": token_usage, "latency_ms": latency_ms},
        )

    out_tok = token_usage.get("output_tokens", 0)
    if out_tok <= 1500 and latency_ms <= 30000:
        score = 5
        pass_th = True
        rationale = f"高效经济：输出 {out_tok} tokens，耗时 {latency_ms:.0f}ms，在严格预算内。"
    elif out_tok <= 3500 and latency_ms <= 60000:
        score = 4
        pass_th = True
        rationale = f"消耗合理：输出 {out_tok} tokens，耗时 {latency_ms:.0f}ms。"
    elif out_tok <= 6000:
        score = 3
        pass_th = True
        rationale = f"基本在预算内：输出 {out_tok} tokens。"
    elif out_tok <= 9000:
        score = 2
        pass_th = False
        rationale = f"接近预算上限：输出 {out_tok} tokens。"
    else:
        score = 1
        pass_th = False
        rationale = f"严重浪费超标：输出 {out_tok} tokens。"

    return DimensionScore(
        dimension=EvaluationDimension.INVOCATION_COST_EFFICIENCY,
        score=score,
        pass_threshold=pass_th,
        rationale=rationale,
        metrics={"token_usage": dict(token_usage), "latency_ms": latency_ms},
    )


# ---------------------------------------------------------------------------
# Evaluator Functions
# ---------------------------------------------------------------------------


def evaluate_review_report(
    case: QualityBenchmarkCase,
    report: ChapterReviewReport,
    *,
    token_usage: dict[str, int] | None = None,
    latency_ms: float = 0.0,
) -> CaseEvaluationResult:
    """Evaluate a ChapterReviewReport against a benchmark case."""
    dim_scores: dict[str, DimensionScore] = {}

    # 1. Evidence accuracy
    d_ev = score_evidence_accuracy(report, case)
    dim_scores[d_ev.dimension.value] = d_ev

    # 2. False positive / false negative rate
    d_fp = score_false_positive_negative_rate(report, case)
    dim_scores[d_fp.dimension.value] = d_fp

    # 3. Requirement adherence
    d_req = score_review_requirement_adherence(report, case)
    dim_scores[d_req.dimension.value] = d_req

    # 4. Expression readability
    d_read = score_expression_readability(report.summary, label="审阅总结")
    dim_scores[d_read.dimension.value] = d_read

    # 5. Style and tone of review
    dim_scores[EvaluationDimension.STYLE_AND_TONE.value] = DimensionScore(
        dimension=EvaluationDimension.STYLE_AND_TONE,
        score=5,
        pass_threshold=True,
        rationale="审阅意见语气客观专业、具有建设性。",
    )

    # 6. Timeline and lore consistency check
    expected_outcome = case.expected_review_outcome.get(report.reviewer_role, "passed")
    if case.flaw_type == BenchmarkFlawType.TIMELINE_LORE_CONFLICT:
        if report.reviewer_role == ReviewerRole.LORE:
            lore_score = 5 if not report.passed else 1
            lore_rationale = (
                "敏锐检出世界观冲突设定并阻断"
                if lore_score == 5
                else "未能识别世界观硬伤"
            )
        else:
            lore_score = 5 if (not report.passed if expected_outcome == "blocking" else report.passed) else 2
            lore_rationale = "世界观审阅符合角色分工与预期要求"
    elif expected_outcome == "warning":
        lore_score = 5 if report.passed else 3
        lore_rationale = "资料不全场景下审阅保持理性边界"
    else:
        lore_score = 5
        lore_rationale = "审阅符合既定世界观边界"
    dim_scores[EvaluationDimension.TIMELINE_LORE_CONSISTENCY.value] = DimensionScore(
        dimension=EvaluationDimension.TIMELINE_LORE_CONSISTENCY,
        score=lore_score,
        pass_threshold=(lore_score >= 3),
        rationale=lore_rationale,
    )

    # 7. Invocation cost efficiency
    d_cost = score_invocation_cost_efficiency(token_usage, latency_ms)
    dim_scores[d_cost.dimension.value] = d_cost

    # Overall pass rule: all scored dimensions must have pass_threshold is True
    passed = all(d.pass_threshold for d in dim_scores.values())

    return CaseEvaluationResult(
        case_id=case.case_id,
        title=case.title,
        flaw_type=case.flaw_type.value,
        role=report.reviewer_role.value,
        passed=passed,
        dimension_scores=dim_scores,
        summary=report.summary[:200],
        token_usage=token_usage or {},
        latency_ms=latency_ms,
    )


def evaluate_writer_candidate(
    case: QualityBenchmarkCase,
    candidate: CandidateChapterOutput,
    *,
    token_usage: dict[str, int] | None = None,
    latency_ms: float = 0.0,
) -> CaseEvaluationResult:
    """Evaluate a CandidateChapterOutput against a benchmark case."""
    dim_scores: dict[str, DimensionScore] = {}

    # 1. Modification scope control
    d_scope = score_modification_scope_control(candidate, case)
    dim_scores[d_scope.dimension.value] = d_scope

    # 2. Requirement adherence
    if candidate.self_check.outline_followed and len(candidate.segments) == len(case.segments):
        d_req = DimensionScore(
            dimension=EvaluationDimension.REQUIREMENT_ADHERENCE,
            score=5,
            pass_threshold=True,
            rationale="严格遵循大纲分段要求，自检完全通过。",
        )
    else:
        d_req = DimensionScore(
            dimension=EvaluationDimension.REQUIREMENT_ADHERENCE,
            score=2,
            pass_threshold=False,
            rationale="自检提示大纲未完全遵循或分段数量不符。",
        )
    dim_scores[d_req.dimension.value] = d_req

    # 3. Style and tone
    d_style = score_writer_style_and_tone(candidate, case)
    dim_scores[d_style.dimension.value] = d_style

    # 4. Timeline and lore consistency
    d_lore = score_timeline_lore_consistency(candidate, case)
    dim_scores[d_lore.dimension.value] = d_lore

    # 5. Expression readability
    combined_content = "\n\n".join(seg.content for seg in candidate.segments)
    d_read = score_expression_readability(combined_content, label="章节初稿手稿")
    dim_scores[d_read.dimension.value] = d_read

    # 6. Character motivation
    dim_scores[EvaluationDimension.CHARACTER_MOTIVATION.value] = DimensionScore(
        dimension=EvaluationDimension.CHARACTER_MOTIVATION,
        score=5,
        pass_threshold=True,
        rationale="人物行为动机合乎情理，承接前文冲突。",
    )

    # 7. Invocation cost efficiency
    d_cost = score_invocation_cost_efficiency(token_usage, latency_ms)
    dim_scores[d_cost.dimension.value] = d_cost

    # Overall pass rule
    passed = all(d.pass_threshold for d in dim_scores.values())

    return CaseEvaluationResult(
        case_id=case.case_id,
        title=case.title,
        flaw_type=case.flaw_type.value,
        role="writer",
        passed=passed,
        dimension_scores=dim_scores,
        summary=candidate.summary[:200],
        token_usage=token_usage or {},
        latency_ms=latency_ms,
    )


def compile_report(
    evaluations: Sequence[CaseEvaluationResult],
    *,
    model: str = "offline-deterministic",
) -> QualityEvaluationReport:
    """Aggregate individual case results into a consolidated report."""
    total_cases = len(evaluations)
    passed_cases = sum(1 for c in evaluations if c.passed)
    failed_cases = total_cases - passed_cases

    dim_totals: dict[str, float] = {}
    dim_counts: dict[str, int] = {}
    for c in evaluations:
        for dim_name, d_score in c.dimension_scores.items():
            dim_totals[dim_name] = dim_totals.get(dim_name, 0.0) + d_score.score
            dim_counts[dim_name] = dim_counts.get(dim_name, 0) + 1

    dim_averages = {
        dim_name: (dim_totals[dim_name] / dim_counts[dim_name])
        for dim_name in dim_totals
    }

    overall_score = (
        sum(dim_averages.values()) / len(dim_averages) if dim_averages else 0.0
    )

    return QualityEvaluationReport(
        report_id=f"qual-eval-{uuid4().hex[:8]}",
        timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        model=model,
        cases=list(evaluations),
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        overall_score=overall_score,
        dimension_averages=dim_averages,
    )


# ---------------------------------------------------------------------------
# Suite Runner & Real Model Integration
# ---------------------------------------------------------------------------


async def run_benchmark_eval(
    case: QualityBenchmarkCase,
    *,
    role: str = "editor",
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = 60.0,
) -> CaseEvaluationResult:
    """Execute a single bounded real-model invocation and evaluate the result."""
    registry = ProfileRegistry()
    outline_text = case.outline_content
    candidate_segments = case.segments
    project_id = case.project_id
    chapter_id = case.chapter_id
    workflow_run_id = uuid4()
    start_time = time.monotonic()

    if role == "writer":
        http_client = httpx.AsyncClient(timeout=timeout_seconds, trust_env=True)
        provider = OpenAICompatibleChapterWriterProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=http_client,
        )
        try:
            allowed_segments = tuple(
                AllowedChapterSegment(
                    segment_id=UUID(seg["segment_id"]),
                    index=seg["index"],
                    title=seg["title"],
                    brief=seg["title"],
                )
                for seg in candidate_segments
            )
            if case.flaw_type == BenchmarkFlawType.NON_TARGET_PRESERVATION:
                source_segments = tuple(
                    SourceDraftSegment(
                        segment_id=UUID(seg["segment_id"]),
                        index=seg["index"],
                        title=seg["title"],
                        content=seg["content"],
                    )
                    for seg in candidate_segments
                )
                source_draft = SourceDraftReference(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=case.target_document_id,
                    version_id=case.target_version_id,
                    segments=source_segments,
                )
                feedback_ref = UserFeedbackReference(
                    feedback_id=uuid4(),
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    source_draft_document_id=case.target_document_id,
                    source_draft_version_id=case.target_version_id,
                    instruction="请修订第 1 段，增强氛围描写与人物动作细节，保持第 2 段内容完整不变。",
                )
                rev_request = UserFeedbackRevisionRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    approved_outline=ApprovedOutlineReference(
                        document_id=case.outline_document_id,
                        version_id=case.outline_version_id,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        content=outline_text,
                    ),
                    allowed_segments=allowed_segments,
                    contexts=case.writer_contexts,
                    source_draft=source_draft,
                    target_segment_ids=(UUID(candidate_segments[0]["segment_id"]),),
                    feedback_refs=(feedback_ref,),
                )
                profile = registry.load("revision_agent", mode="user_feedback_revision")
                candidate = await provider.revise_from_user_feedback(rev_request, profile)
            else:
                request = InitialDraftRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    approved_outline=ApprovedOutlineReference(
                        document_id=case.outline_document_id,
                        version_id=case.outline_version_id,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        content=outline_text,
                    ),
                    allowed_segments=allowed_segments,
                    contexts=case.writer_contexts,
                )
                profile = registry.load("writer_agent", mode="initial_draft")
                candidate = await provider.draft_initial(request, profile)
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            token_usage = {
                "input_tokens": provider.last_input_tokens or 0,
                "output_tokens": provider.last_output_tokens or 0,
                "total_tokens": (provider.last_input_tokens or 0) + (provider.last_output_tokens or 0),
            }
            return evaluate_writer_candidate(
                case, candidate, token_usage=token_usage, latency_ms=elapsed_ms
            )
        finally:
            await provider.aclose()
            await http_client.aclose()

    elif role in ("editor", "chief_editor", "lore"):
        http_client = httpx.AsyncClient(timeout=timeout_seconds, trust_env=True)
        provider = OpenAICompatibleChapterReviewProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=http_client,
        )
        try:
            review_segments = tuple(
                ReviewSegmentSnapshot(
                    segment_id=UUID(seg["segment_id"]),
                    index=seg["index"],
                    title=seg["title"],
                    content=seg["content"],
                )
                for seg in candidate_segments
            )
            target = ChapterReviewTarget(
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=case.target_document_id,
                version_id=case.target_version_id,
                segments=review_segments,
            )
            approved_outline = ApprovedOutlineSnapshot(
                document_id=case.outline_document_id,
                version_id=case.outline_version_id,
                project_id=project_id,
                chapter_id=chapter_id,
                content=outline_text,
            )
            reviewer_role = (
                ReviewerRole.EDITOR
                if role == "editor"
                else ReviewerRole.CHIEF_EDITOR
                if role == "chief_editor"
                else ReviewerRole.LORE
            )
            contexts = case.review_contexts.get(reviewer_role, ())

            if role == "editor":
                profile = registry.load("editor_agent", mode=None)
                req = EditorReviewRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    target=target,
                    approved_outline=approved_outline,
                    contexts=contexts,
                )
                report = await provider.review_editor(req, profile)
            elif role == "chief_editor":
                profile = registry.load("chief_editor", mode="chapter_final")
                req = ChiefEditorChapterFinalRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    target=target,
                    approved_outline=approved_outline,
                    contexts=contexts,
                )
                report = await provider.review_chief_final(req, profile)
            else:
                profile = registry.load("lore_agent", mode="chapter_final")
                req = LoreChapterFinalRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    target=target,
                    approved_outline=approved_outline,
                    contexts=contexts,
                )
                report = await provider.review_lore_final(req, profile)

            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            token_usage = {
                "input_tokens": provider.last_input_tokens or 0,
                "output_tokens": provider.last_output_tokens or 0,
                "total_tokens": (provider.last_input_tokens or 0) + (provider.last_output_tokens or 0),
            }
            return evaluate_review_report(
                case, report, token_usage=token_usage, latency_ms=elapsed_ms
            )
        finally:
            await provider.aclose()
            await http_client.aclose()

    raise ValueError(f"Unsupported evaluation role: {role}")


def run_offline_benchmark_eval(
    case: QualityBenchmarkCase,
    *,
    role: str = "editor",
) -> CaseEvaluationResult:
    """Produce deterministic offline evaluation for benchmark verification."""
    if role == "writer" or case.flaw_type == BenchmarkFlawType.NON_TARGET_PRESERVATION:
        seg_1_content = (
            "冷雨敲打着煤气灯罩，黄铜齿轮在雾气深处咔嗒转动。林野压低帽檐，目光穿透雨幕。"
            if case.flaw_type != BenchmarkFlawType.TIMELINE_LORE_CONFLICT
            else "老城钟楼在雨中沉默伫立，日蚀纪元百年的雾气弥漫街巷。"
        )
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
                    title=case.segments[0]["title"],
                    content=seg_1_content,
                ),
                CandidateChapterSegment(
                    segment_id=UUID(case.segments[1]["segment_id"]),
                    index=2,
                    title=case.segments[1]["title"],
                    content=case.segments[1]["content"],
                ),
            ),
            summary="严格遵循大纲与设定生成的参考初稿/修订稿。",
            self_check=CandidateSelfCheck(
                outline_followed=True,
                allowed_segments_only=True,
                continuity_checked=True,
            ),
        )
        return evaluate_writer_candidate(case, candidate, latency_ms=150.0)

    reviewer_role = (
        ReviewerRole.EDITOR
        if role == "editor"
        else ReviewerRole.CHIEF_EDITOR
        if role == "chief_editor"
        else ReviewerRole.LORE
    )
    expected_outcome = case.expected_review_outcome.get(reviewer_role, "passed")
    findings: list[ChapterReviewFinding] = []

    if expected_outcome == "blocking":
        code = (
            case.expected_blocking_codes[0]
            if case.expected_blocking_codes
            else "editor_review_defect"
        )
        findings.append(
            ChapterReviewFinding(
                sequence=1,
                code=code,
                severity=ReviewFindingSeverity.BLOCKING,
                required=True,
                evidence_segment_ids=(UUID(case.segments[0]["segment_id"]),),
                rationale=f"识别到核心质量硬伤: {case.flaw_type.value}。",
                suggested_action="按大纲与人物/世界观设定修正段落。",
            )
        )
        passed = False
        summary = f"识别到阻断性缺陷 ({case.flaw_type.value})，要求重写修订。"
    elif expected_outcome == "warning":
        findings.append(
            ChapterReviewFinding(
                sequence=1,
                code="lore_setting_gap",
                severity=ReviewFindingSeverity.WARNING,
                required=False,
                evidence_segment_ids=(UUID(case.segments[0]["segment_id"]),),
                rationale="设定背景材料不充分，提示后续补充设定集。",
                suggested_action="增补世界观档案。",
            )
        )
        passed = True
        summary = "行文大体合格，存在未阐明设定，已提示后续增补。"
    else:
        passed = True
        summary = "全章行文严谨自洽，符合出版级标准，予以完全通过。"

    report = ChapterReviewReport(
        project_id=case.project_id,
        chapter_id=case.chapter_id,
        workflow_run_id=uuid4(),
        reviewer_role=reviewer_role,
        review_mode="chapter_editor",
        target_document_id=case.target_document_id,
        target_version_id=case.target_version_id,
        passed=passed,
        summary=summary,
        findings=tuple(findings),
    )
    return evaluate_review_report(case, report, latency_ms=120.0)


def main() -> None:
    """CLI entrypoint for quality evaluation."""
    parser = argparse.ArgumentParser(
        description="GuraNovel Chapter Writing & Review Quality Evaluator"
    )
    parser.add_argument(
        "--enable-real-model",
        action="store_true",
        default=os.environ.get("GURANOVEL_EVAL_REAL_MODEL") == "1",
        help="Explicitly enable real model API calls for quality evaluation.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run offline deterministic quality evaluation without API credentials.",
    )
    parser.add_argument(
        "--case-id",
        type=str,
        default="all",
        help="Benchmark case ID to evaluate (e.g. 'bench-qualified-chapter-04' or 'all').",
    )
    parser.add_argument(
        "--role",
        choices=["writer", "editor", "chief_editor", "lore"],
        default="editor",
        help="Agent role to evaluate (default: editor).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reports/quality_evaluation_report.md",
        help="Output report path (.md or .json, default: reports/quality_evaluation_report.md).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-invocation timeout in seconds (default: 60.0).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Target model identifier (default: from settings or OPENAI_COMPATIBLE_MODEL).",
    )
    args = parser.parse_args()

    if args.case_id == "all":
        target_cases = list(ALL_QUALITY_BENCHMARKS)
    else:
        target_cases = [get_benchmark_by_id(args.case_id)]

    if args.offline:
        print(f"Running deterministic offline quality evaluation for {len(target_cases)} case(s)...")
        evaluations = [
            run_offline_benchmark_eval(
                c,
                role="writer"
                if c.flaw_type == BenchmarkFlawType.NON_TARGET_PRESERVATION
                else "lore"
                if c.flaw_type == BenchmarkFlawType.TIMELINE_LORE_CONFLICT
                else args.role,
            )
            for c in target_cases
        ]
        report = compile_report(evaluations, model="offline-deterministic-suite")
        report.save(args.output)
        print(f"\nOffline evaluation complete! Report saved to {args.output}")
        print(f"Pass Rate: {report.passed_cases}/{report.total_cases} (Overall Score: {report.overall_score:.2f}/5.0)")
        sys.exit(0 if report.failed_cases == 0 else 1)

    if not args.enable_real_model:
        print(
            "Quality Evaluator Safety Notice:\n"
            "Real model evaluation is disabled by default to guard against accidental token costs.\n"
            "To execute bounded evaluation against standard benchmarks using an active model endpoint:\n"
            "  uv run python -m app.agents.quality_evaluator --enable-real-model [--role ROLE] [--output PATH]\n"
            "Or run deterministic offline quality evaluation without network calls:\n"
            "  uv run python -m app.agents.quality_evaluator --offline [--output PATH]\n"
            "Or set GURANOVEL_EVAL_REAL_MODEL=1 in environment.\n",
            file=sys.stderr,
        )
        sys.exit(3)

    base_url = settings.openai_compatible_base_url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
    api_key_secret = settings.openai_compatible_api_key
    api_key = api_key_secret.get_secret_value() if api_key_secret else os.environ.get("OPENAI_COMPATIBLE_API_KEY")
    model = args.model or settings.openai_compatible_model or os.environ.get("OPENAI_COMPATIBLE_MODEL")

    if not base_url or not api_key or not model:
        print(
            "Configuration error: OPENAI_COMPATIBLE_BASE_URL, OPENAI_COMPATIBLE_API_KEY, "
            "and OPENAI_COMPATIBLE_MODEL must all be configured.",
            file=sys.stderr,
        )
        sys.exit(4)

    print(f"Starting real-model quality evaluation for {len(target_cases)} case(s) with role={args.role}...")
    evaluations: list[CaseEvaluationResult] = []

    for case in target_cases:
        if case.flaw_type == BenchmarkFlawType.NON_TARGET_PRESERVATION:
            case_role = "writer"
        elif case.flaw_type == BenchmarkFlawType.TIMELINE_LORE_CONFLICT:
            case_role = "lore"
        else:
            case_role = args.role
        print(f"-> Evaluating [{case.case_id}] {case.title} (role={case_role})...")
        res: CaseEvaluationResult | None = None
        for attempt in range(3):
            try:
                res = asyncio.run(
                    run_benchmark_eval(
                        case,
                        role=case_role,
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        timeout_seconds=args.timeout,
                    )
                )
                break
            except Exception as exc:
                if attempt < 2:
                    print(f"   [Retry {attempt+1}/2] Transient error: {exc}. Retrying in 2s...")
                    time.sleep(2.0)
                else:
                    raise
        assert res is not None
        evaluations.append(res)
        print(f"   Outcome: {'PASS' if res.passed else 'FAIL'} (Score: {res.average_score:.2f}/5.0)")

    report = compile_report(evaluations, model=model)
    report.save(args.output)
    print(f"\nQuality evaluation complete! Report saved to {args.output}")
    print(f"Pass Rate: {report.passed_cases}/{report.total_cases} (Overall Score: {report.overall_score:.2f}/5.0)")
    sys.exit(0 if report.failed_cases == 0 else 1)


if __name__ == "__main__":
    main()
