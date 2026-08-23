"""Unit tests for deterministic Reader Panel fakes and scenario providers."""

from __future__ import annotations

from uuid import uuid4
import pytest

from app.agents.reader_panel_contracts import (
    Confidence,
    ContinueReadingVote,
    ExtractedIssueItem,
    EvidenceRef,
    ReaderBlindBallotRequest,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotRequest,
    ReaderInitialReadingRequest,
    ModeratorDiscussionSummaryRequest,
    ModeratorReportSynthesisRequest,
    Severity,
    TargetAudienceRelevance,
)
from app.agents.reader_panel_fakes import (
    DeterministicReaderPanelProvider,
    ReaderPanelFakeScenario,
)
from app.llm.errors import ProviderInvalidOutputError


class TestDeterministicReaderPanelProvider:
    def test_clean_scenario_initial_reading(self) -> None:
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.CLEAN)
        req = ReaderInitialReadingRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Scene 1", "S002": "Scene 2"},
            test_goals=["Check flow"],
        )
        res = provider.generate_initial_reading(req)
        assert res.continue_reading == ContinueReadingVote.YES
        assert res.confidence == Confidence.HIGH
        assert len(res.strengths) >= 1

    def test_disagreement_scenario_ballots_and_discussion(self) -> None:
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.DISAGREEMENT)
        issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue pacing",
            category="pacing",
            symptom="Dialogue feels prolonged",
            root_cause_hypotheses=["Exposition in dialogue"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Dialogue")],
            source_reader_ids=["low_patience"],
            target_audience_relevance=TargetAudienceRelevance.MEDIUM,
            minority_risk=False,
        )

        req_low = ReaderBlindBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="low_patience",
            issue=issue,
            manuscript_segments={"S002": "Text"},
        )
        ballot_low = provider.generate_blind_ballot(req_low)
        assert ballot_low.severity in (Severity.SIGNIFICANT, Severity.CRITICAL)

        req_imm = ReaderBlindBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=issue,
            manuscript_segments={"S002": "Text"},
        )
        ballot_imm = provider.generate_blind_ballot(req_imm)
        assert ballot_imm.severity in (Severity.NONE, Severity.MINOR)

    def test_minority_high_risk_scenario(self) -> None:
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.MINORITY_HIGH_RISK)
        issue = ExtractedIssueItem(
            issue_number=1,
            title="Premature twist revelation",
            category="plot",
            symptom="Foreshadowing gives away the traitor immediately",
            root_cause_hypotheses=["Too obvious clue in dialogue"],
            evidence=[EvidenceRef(segment_ids=["S001"], note="Clue")],
            source_reader_ids=["genre_experienced"],
            target_audience_relevance=TargetAudienceRelevance.HIGH,
            minority_risk=True,
        )
        req = ReaderBlindBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="genre_experienced",
            issue=issue,
            manuscript_segments={"S001": "Clue"},
        )
        ballot = provider.generate_blind_ballot(req)
        assert ballot.severity == Severity.CRITICAL
        assert ballot.confidence == Confidence.HIGH

    def test_malformed_output_scenario_raises_safe_error(self) -> None:
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.MALFORMED_OUTPUT)
        req = ReaderInitialReadingRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="fantasy",
            target_audience=["general"],
            manuscript_segments={"S001": "Text"},
            test_goals=[],
        )
        with pytest.raises(ProviderInvalidOutputError):
            provider.generate_initial_reading(req)


    def test_full_discussion_and_synthesis_cycle(self) -> None:
        provider = DeterministicReaderPanelProvider(scenario=ReaderPanelFakeScenario.CLEAN)
        issue = ExtractedIssueItem(
            issue_number=1,
            title="Minor phrasing repetition",
            category="style",
            symptom="Descriptive wording repeated",
            root_cause_hypotheses=["Stylistic draft repetition"],
            evidence=[EvidenceRef(segment_ids=["S002"], note="Repetition")],
            source_reader_ids=["general_immersive"],
            target_audience_relevance=TargetAudienceRelevance.LOW,
            minority_risk=False,
        )

        turn_req = ReaderDiscussionTurnRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=issue,
            round_number=1,
            turn_number=1,
            prior_messages=[],
            prior_ballot={"severity": "minor", "suggested_action": "clarify"},
            manuscript_segments={"S002": "Text"},
        )
        turn_out = provider.generate_discussion_turn(turn_req)
        assert turn_out.stance.value in ("support", "oppose", "mixed", "abstain")
        assert len(turn_out.evidence) >= 1

        sum_req = ModeratorDiscussionSummaryRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            issue=issue,
            round_number=1,
            round_messages=[{"turn": 1, "claim": turn_out.claim}],
        )
        sum_out = provider.summarize_discussion(sum_req)
        assert sum_out.round_summary != ""

        final_ballot_req = ReaderFinalBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            issue=issue,
            round_summaries=[sum_out.round_summary],
            initial_ballot={"severity": "minor", "suggested_action": "clarify"},
            manuscript_segments={"S002": "Text"},
        )
        final_ballot_out = provider.generate_final_ballot(final_ballot_req)
        assert final_ballot_out.issue_number == 1

        syn_req = ModeratorReportSynthesisRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            initial_reports={"general_immersive": {"overall_reaction": "Good."}},
            extracted_issues=[issue],
            final_consensus_results={1: {"consensus_class": "strong_consensus"}},
            minority_risk_issues=[],
        )
        report_out = provider.synthesize_report(syn_req)
        assert report_out.executive_summary != ""
        assert len(report_out.key_findings) == 1
