"""Deterministic test fakes and scenario providers for Reader Panel agents."""

from __future__ import annotations

from enum import Enum

from app.agents.reader_panel_contracts import (
    ActionableRecommendationItem,
    Confidence,
    ConsensusClass,
    ContinueReadingVote,
    DiscussionNovelty,
    DiscussionStance,
    EditorialDecision,
    EvidenceRef,
    ExtractedIssueItem,
    KeyFindingItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorDiscussionSummaryRequest,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisOutput,
    ModeratorReportSynthesisRequest,
    ReaderBallotOutput,
    ReaderBlindBallotRequest,
    ReaderDiscussionTurnOutput,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotOutput,
    ReaderFinalBallotRequest,
    ReaderInitialReadingOutput,
    ReaderInitialReadingRequest,
    Severity,
    SuggestedAction,
    TargetAudienceRelevance,
)
from app.llm.errors import ProviderInvalidOutputError


class ReaderPanelFakeScenario(str, Enum):
    CLEAN = "clean"
    DISAGREEMENT = "disagreement"
    MINORITY_HIGH_RISK = "minority_high_risk"
    DEGRADED = "degraded"
    MALFORMED_OUTPUT = "malformed_output"
    BUDGET_EXCEEDED = "budget_exceeded"


class DeterministicReaderPanelProvider:
    """Deterministic provider generating strict, valid reader panel responses for tests."""

    def __init__(self, scenario: ReaderPanelFakeScenario = ReaderPanelFakeScenario.CLEAN) -> None:
        self.scenario = scenario

    def generate_initial_reading(self, request: ReaderInitialReadingRequest) -> ReaderInitialReadingOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        seg_keys = list(request.manuscript_segments.keys())
        first_seg = seg_keys[0] if seg_keys else "S001"
        second_seg = seg_keys[1] if len(seg_keys) > 1 else first_seg

        if self.scenario == ReaderPanelFakeScenario.CLEAN:
            return ReaderInitialReadingOutput(
                overall_reaction=f"Strong, engaging narrative flow from reader {request.reader_profile_id}.",
                continue_reading=ContinueReadingVote.YES,
                confidence=Confidence.HIGH,
                strengths=[
                    {
                        "summary": "Compelling conflict hook and smooth character introduction.",
                        "evidence": [{"segment_ids": [first_seg], "note": "Hook"}],
                    }
                ],
                reactions=[
                    {
                        "segment_ids": [first_seg],
                        "reaction": "Immediately drawn into the scene stakes.",
                        "emotion": "engaged",
                    }
                ],
                concerns=[
                    {
                        "category": "pacing",
                        "symptom": "Minor phrasing repetition in description.",
                        "severity": Severity.MINOR,
                        "evidence": [{"segment_ids": [second_seg], "note": "Repetition"}],
                        "suggested_action": SuggestedAction.COMPRESS,
                    }
                ],
            )
        elif self.scenario == ReaderPanelFakeScenario.MINORITY_HIGH_RISK:
            is_risk_reader = request.reader_profile_id in ("genre_experienced", "low_patience")
            return ReaderInitialReadingOutput(
                overall_reaction=(
                    "Identified a fatal plot foreshadowing leak that ruins the chapter twist."
                    if is_risk_reader
                    else "Overall enjoyable read."
                ),
                continue_reading=ContinueReadingVote.MAYBE if is_risk_reader else ContinueReadingVote.YES,
                confidence=Confidence.HIGH,
                strengths=[
                    {
                        "summary": "Solid prose cadence.",
                        "evidence": [{"segment_ids": [first_seg], "note": "Prose"}],
                    }
                ],
                reactions=[],
                concerns=[
                    {
                        "category": "plot" if is_risk_reader else "pacing",
                        "symptom": (
                            "Foreshadowing dialogue unmasks the hidden traitor prematurely."
                            if is_risk_reader
                            else "Minor exposition drag."
                        ),
                        "severity": Severity.CRITICAL if is_risk_reader else Severity.MINOR,
                        "evidence": [{"segment_ids": [first_seg if is_risk_reader else second_seg], "note": "Risk"}],
                        "suggested_action": SuggestedAction.REWRITE_LOCAL if is_risk_reader else SuggestedAction.KEEP,
                    }
                ],
            )
        elif self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            is_critical = request.reader_profile_id in ("low_patience", "style_sensitive")
            return ReaderInitialReadingOutput(
                overall_reaction=(
                    "Pacing feels dragged down by verbose dialogue explanations."
                    if is_critical
                    else "Immersive scene pacing with appropriate detail."
                ),
                continue_reading=ContinueReadingVote.NO if is_critical else ContinueReadingVote.YES,
                confidence=Confidence.HIGH,
                strengths=[
                    {
                        "summary": "Atmospheric world setup.",
                        "evidence": [{"segment_ids": [first_seg], "note": "Atmosphere"}],
                    }
                ],
                reactions=[],
                concerns=[
                    {
                        "category": "pacing",
                        "symptom": "Dialogue exposition slows combat momentum.",
                        "severity": Severity.SIGNIFICANT if is_critical else Severity.NONE,
                        "evidence": [{"segment_ids": [second_seg], "note": "Dialogue"}],
                        "suggested_action": SuggestedAction.COMPRESS if is_critical else SuggestedAction.KEEP,
                    }
                ],
            )
        else:  # DEGRADED or default
            return ReaderInitialReadingOutput(
                overall_reaction="Acceptable draft.",
                continue_reading=ContinueReadingVote.MAYBE,
                confidence=Confidence.MEDIUM,
                strengths=[],
                reactions=[],
                concerns=[],
            )

    def extract_issues(self, request: ModeratorIssueExtractionRequest) -> ModeratorIssueExtractionOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        seg_keys = list(request.manuscript_segments.keys())
        first_seg = seg_keys[0] if seg_keys else "S001"
        second_seg = seg_keys[1] if len(seg_keys) > 1 else first_seg

        if self.scenario == ReaderPanelFakeScenario.MINORITY_HIGH_RISK:
            return ModeratorIssueExtractionOutput(
                issues=[
                    ExtractedIssueItem(
                        issue_number=1,
                        title="Premature twist revelation in dialogue",
                        category="plot",
                        symptom="Foreshadowing gives away traitor identity early",
                        root_cause_hypotheses=["Dialogue clues too explicit before the reveal"],
                        evidence=[EvidenceRef(segment_ids=[first_seg], note="Twist clue")],
                        source_reader_ids=["genre_experienced"],
                        target_audience_relevance=TargetAudienceRelevance.HIGH,
                        minority_risk=True,
                    )
                ]
            )
        elif self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            return ModeratorIssueExtractionOutput(
                issues=[
                    ExtractedIssueItem(
                        issue_number=1,
                        title="Dialogue pacing and exposition load",
                        category="pacing",
                        symptom="Exposition in dialogue slows scene tempo",
                        root_cause_hypotheses=["Lore inserted mid-conversation"],
                        evidence=[EvidenceRef(segment_ids=[second_seg], note="Dialogue lore")],
                        source_reader_ids=["low_patience", "style_sensitive"],
                        target_audience_relevance=TargetAudienceRelevance.MEDIUM,
                        minority_risk=False,
                    )
                ]
            )
        else:  # CLEAN or default
            return ModeratorIssueExtractionOutput(
                issues=[
                    ExtractedIssueItem(
                        issue_number=1,
                        title="Minor phrasing repetition",
                        category="style",
                        symptom="Descriptive wording repeated in close proximity",
                        root_cause_hypotheses=["Stylistic draft repetition"],
                        evidence=[EvidenceRef(segment_ids=[second_seg], note="Repetition")],
                        source_reader_ids=["general_immersive"],
                        target_audience_relevance=TargetAudienceRelevance.LOW,
                        minority_risk=False,
                    )
                ]
            )

    def generate_blind_ballot(self, request: ReaderBlindBallotRequest) -> ReaderBallotOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        seg_keys = list(request.manuscript_segments.keys())
        first_seg = seg_keys[0] if seg_keys else "S001"

        if self.scenario == ReaderPanelFakeScenario.MINORITY_HIGH_RISK:
            is_risk_reader = request.reader_profile_id in ("genre_experienced", "low_patience")
            return ReaderBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.CRITICAL if is_risk_reader else Severity.MINOR,
                suggested_action=SuggestedAction.REWRITE_LOCAL if is_risk_reader else SuggestedAction.KEEP,
                confidence=Confidence.HIGH,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Ballot evidence")],
                reason="Severe risk of killing dramatic surprise." if is_risk_reader else "Not perceived as blocking.",
            )
        elif self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            is_critical = request.reader_profile_id in ("low_patience", "style_sensitive")
            return ReaderBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.SIGNIFICANT if is_critical else Severity.MINOR,
                suggested_action=SuggestedAction.COMPRESS if is_critical else SuggestedAction.KEEP,
                confidence=Confidence.HIGH if is_critical else Confidence.MEDIUM,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Ballot evidence")],
                reason="Pacing suffers significantly." if is_critical else "Minor stylistic detail.",
            )
        else:  # CLEAN or default
            return ReaderBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.MINOR,
                suggested_action=SuggestedAction.CLARIFY,
                confidence=Confidence.MEDIUM,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Ballot evidence")],
                reason="Minor polish would improve the scene.",
            )

    def generate_discussion_turn(self, request: ReaderDiscussionTurnRequest) -> ReaderDiscussionTurnOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        seg_keys = list(request.manuscript_segments.keys())
        first_seg = seg_keys[0] if seg_keys else "S001"

        if self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            is_critical = request.reader_profile_id in ("low_patience", "style_sensitive")
            return ReaderDiscussionTurnOutput(
                stance=DiscussionStance.SUPPORT if is_critical else DiscussionStance.OPPOSE,
                claim=(
                    "The scene momentum completely halts during the middle paragraphs."
                    if is_critical
                    else "The dialogue builds necessary tension before the action resumes."
                ),
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Discussion turn evidence")],
                concession=(
                    "I concede the lore is useful, but the placement is wrong."
                    if is_critical
                    else "I agree it could be tightened slightly."
                ),
                proposed_action="Compress dialogue by 30%." if is_critical else "Keep dialogue structure intact.",
                novelty=DiscussionNovelty.NEW_INTERPRETATION,
            )
        else:
            return ReaderDiscussionTurnOutput(
                stance=DiscussionStance.SUPPORT,
                claim="A light trim will resolve the minor phrasing repetition smoothly.",
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Discussion turn evidence")],
                concession=None,
                proposed_action="Trim duplicate adjectives.",
                novelty=DiscussionNovelty.NEW_EVIDENCE,
            )

    def summarize_discussion(self, request: ModeratorDiscussionSummaryRequest) -> ModeratorDiscussionSummaryOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        if self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            return ModeratorDiscussionSummaryOutput(
                round_summary="Readers diverge on whether dialogue exposition enhances tension or halts momentum.",
                remaining_disagreements=["Degree of compression required for scene dialogue."],
                suggested_focus="Evaluate targeted compression while preserving key character revelations.",
                is_consensus_reached=False,
            )
        else:
            return ModeratorDiscussionSummaryOutput(
                round_summary="Consensus reached that minor adjective trimming solves the repetition.",
                remaining_disagreements=[],
                suggested_focus="Finalize light phrasing polish.",
                is_consensus_reached=True,
            )

    def generate_final_ballot(self, request: ReaderFinalBallotRequest) -> ReaderFinalBallotOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        seg_keys = list(request.manuscript_segments.keys())
        first_seg = seg_keys[0] if seg_keys else "S001"

        if self.scenario == ReaderPanelFakeScenario.MINORITY_HIGH_RISK:
            is_risk = request.reader_profile_id in ("genre_experienced", "low_patience")
            return ReaderFinalBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.CRITICAL if is_risk else Severity.MINOR,
                suggested_action=SuggestedAction.REWRITE_LOCAL if is_risk else SuggestedAction.KEEP,
                confidence=Confidence.HIGH,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Final ballot evidence")],
                position_changed=False,
                change_reason=None,
                remaining_disagreement="Traitor clue remains too overt." if is_risk else None,
            )
        elif self.scenario == ReaderPanelFakeScenario.DISAGREEMENT:
            is_critical = request.reader_profile_id in ("low_patience", "style_sensitive")
            return ReaderFinalBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.SIGNIFICANT if is_critical else Severity.MINOR,
                suggested_action=SuggestedAction.COMPRESS if is_critical else SuggestedAction.CLARIFY,
                confidence=Confidence.HIGH,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Final ballot evidence")],
                position_changed=True if not is_critical else False,
                change_reason="Acknowledged peer points regarding dialogue pacing." if not is_critical else None,
                remaining_disagreement="Still prefer moderate compression." if is_critical else None,
            )
        else:
            return ReaderFinalBallotOutput(
                issue_number=request.issue.issue_number,
                severity=Severity.MINOR,
                suggested_action=SuggestedAction.CLARIFY,
                confidence=Confidence.HIGH,
                evidence=[EvidenceRef(segment_ids=[first_seg], note="Final ballot evidence")],
                position_changed=False,
                change_reason=None,
                remaining_disagreement=None,
            )

    def synthesize_report(self, request: ModeratorReportSynthesisRequest) -> ModeratorReportSynthesisOutput:
        if self.scenario == ReaderPanelFakeScenario.MALFORMED_OUTPUT:
            raise ProviderInvalidOutputError()

        return ModeratorReportSynthesisOutput(
            executive_summary="Reader Panel evaluated chapter draft. Overall reader continuation willingness is strong.",
            target_audience_appeal="Strong alignment with target genre expectations.",
            key_findings=[
                KeyFindingItem(
                    issue_number=issue.issue_number,
                    title=issue.title,
                    consensus_class=ConsensusClass.STRONG_CONSENSUS if self.scenario == ReaderPanelFakeScenario.CLEAN else ConsensusClass.POLARIZED,
                    recommended_priority=EditorialDecision.MUST_FIX if issue.minority_risk else EditorialDecision.EXPERIMENT,
                    summary=issue.symptom,
                    evidence=issue.evidence,
                )
                for issue in request.extracted_issues
            ],
            actionable_recommendations=[
                ActionableRecommendationItem(
                    priority=EditorialDecision.MUST_FIX if issue.minority_risk else EditorialDecision.EXPERIMENT,
                    target_segment_ids=[ev.segment_ids[0] for ev in issue.evidence if ev.segment_ids] or ["S001"],
                    suggested_action=SuggestedAction.REWRITE_LOCAL if issue.minority_risk else SuggestedAction.COMPRESS,
                    instruction=f"Address {issue.title} according to reader panel feedback.",
                )
                for issue in request.extracted_issues
            ],
        )
