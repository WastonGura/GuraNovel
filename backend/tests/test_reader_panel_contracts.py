"""Unit and contract tests for Reader Panel agent requests, outputs, and validation boundaries."""

from __future__ import annotations

from uuid import uuid4
import pytest
from pydantic import ValidationError

from app.agents.reader_panel_agents import (
    build_blind_ballot_request,
    build_cold_read_request,
)
from app.agents.reader_panel_contracts import (
    Confidence,
    ContinueReadingVote,
    DiscussionNovelty,
    DiscussionStance,
    EvidenceRef,
    ExtractedIssueItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisOutput,
    ReaderBallotOutput,
    ReaderBlindBallotRequest,
    ReaderDiscussionTurnOutput,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotOutput,
    ReaderInitialReadingOutput,
    ReaderInitialReadingRequest,
    Severity,
    SuggestedAction,
    TargetAudienceRelevance,
    validate_reader_panel_text,
)


class TestReaderPanelEnums:
    def test_severity_values(self) -> None:
        assert [s.value for s in Severity] == [
            "none",
            "minor",
            "significant",
            "critical",
            "abstain",
        ]

    def test_suggested_action_values(self) -> None:
        assert [a.value for a in SuggestedAction] == [
            "keep",
            "clarify",
            "compress",
            "expand",
            "move",
            "rewrite_local",
            "split",
            "experiment_ab",
            "manual_review",
        ]

    def test_confidence_values(self) -> None:
        assert [c.value for c in Confidence] == ["low", "medium", "high"]

    def test_continue_reading_values(self) -> None:
        assert [v.value for v in ContinueReadingVote] == ["yes", "maybe", "no"]

    def test_discussion_stance_values(self) -> None:
        assert [s.value for s in DiscussionStance] == [
            "support",
            "oppose",
            "mixed",
            "abstain",
        ]

    def test_discussion_novelty_values(self) -> None:
        assert [n.value for n in DiscussionNovelty] == [
            "new_evidence",
            "new_interpretation",
            "repetition",
            "procedural",
        ]

    def test_target_audience_relevance_values(self) -> None:
        assert [r.value for r in TargetAudienceRelevance] == ["low", "medium", "high"]


class TestSanitizedTextValidation:
    def test_valid_text(self) -> None:
        assert validate_reader_panel_text("The pacing in scene 2 is quick.") == "The pacing in scene 2 is quick."

    def test_rejects_empty_or_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            validate_reader_panel_text("   ")

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_reader_panel_text("Bad text\x00here")

    def test_rejects_credential_leakage(self) -> None:
        with pytest.raises(ValueError, match="forbidden material"):
            validate_reader_panel_text("api_key=sk-12345678abcdefgh")

    def test_rejects_file_system_paths(self) -> None:
        with pytest.raises(ValueError, match="forbidden material"):
            validate_reader_panel_text("Check path /etc/passwd or C:\\Windows\\temp")


class TestReaderInitialReadingContract:
    def test_valid_cold_read_request(self) -> None:
        req = ReaderInitialReadingRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="general_immersive",
            genre="xianxia",
            target_audience=["young_adult", "progression_fantasy"],
            manuscript_segments={"S001": "Opening paragraph text.", "S002": "Second paragraph text."},
            test_goals=["Check if the opening hook is engaging."],
        )
        assert req.reader_profile_id == "general_immersive"
        assert "S001" in req.manuscript_segments

    def test_cold_read_request_forbids_peer_reports_or_other_readers(self) -> None:
        with pytest.raises(ValidationError):
            ReaderInitialReadingRequest(
                project_id=uuid4(),
                chapter_id=uuid4(),
                workflow_run_id=uuid4(),
                reader_profile_id="general_immersive",
                genre="xianxia",
                target_audience=["young_adult"],
                manuscript_segments={"S001": "Text."},
                peer_reports={"other_reader": "report"},  # extra field
            )

    def test_valid_initial_reading_output(self) -> None:
        out = ReaderInitialReadingOutput(
            overall_reaction="Strong momentum in the opening scene.",
            continue_reading=ContinueReadingVote.YES,
            confidence=Confidence.HIGH,
            strengths=[
                {"summary": "Compelling conflict hook.", "evidence": [{"segment_ids": ["S001"], "note": "Hook"}]}
            ],
            reactions=[
                {"segment_ids": ["S001"], "reaction": "Felt excited by the sudden combat.", "emotion": "excited"}
            ],
            concerns=[
                {
                    "category": "pacing",
                    "symptom": "Slight slowdown in dialogue explanation.",
                    "severity": Severity.MINOR,
                    "evidence": [{"segment_ids": ["S002"], "note": "Slowdown"}],
                    "suggested_action": SuggestedAction.COMPRESS,
                }
            ],
        )
        assert out.continue_reading == ContinueReadingVote.YES
        assert len(out.strengths) == 1
        assert len(out.concerns) == 1

    def test_initial_reading_output_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ReaderInitialReadingOutput(
                overall_reaction="Good.",
                continue_reading=ContinueReadingVote.YES,
                confidence=Confidence.HIGH,
                strengths=[],
                reactions=[],
                concerns=[],
                extra_ballot={"issue": "unextracted"},
            )


class TestModeratorIssueExtractionContract:
    def test_valid_issue_extraction_request_and_output(self) -> None:
        req = ModeratorIssueExtractionRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_initial_reports={
                "general_immersive": {
                    "overall_reaction": "Good pacing.",
                    "continue_reading": "yes",
                    "confidence": "high",
                    "strengths": [{"summary": "Hook", "evidence": [{"segment_ids": ["S001"], "note": "Hook"}]}],
                    "reactions": [],
                    "concerns": [
                        {
                            "category": "pacing",
                            "symptom": "Slow paragraph 2",
                            "severity": "minor",
                            "evidence": [{"segment_ids": ["S002"], "note": "Slow"}],
                            "suggested_action": "compress",
                        }
                    ],
                }
            },
            manuscript_segments={"S001": "P1", "S002": "P2"},
            max_ballot_issues=5,
        )
        assert req.max_ballot_issues == 5

        out = ModeratorIssueExtractionOutput(
            issues=[
                ExtractedIssueItem(
                    issue_number=1,
                    title="Expository drag in segment 2",
                    category="pacing",
                    symptom="Exposition interrupts dialogue tension",
                    root_cause_hypotheses=["Too much background lore inserted at once"],
                    evidence=[EvidenceRef(segment_ids=["S002"], note="Exposition block")],
                    source_reader_ids=["general_immersive"],
                    target_audience_relevance=TargetAudienceRelevance.MEDIUM,
                    minority_risk=False,
                )
            ]
        )
        assert len(out.issues) == 1
        assert out.issues[0].issue_number == 1
        assert out.issues[0].source_reader_ids == ["general_immersive"]


class TestBlindInitialBallotContract:
    def test_blind_initial_ballot_request_isolation(self) -> None:
        req = ReaderBlindBallotRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="low_patience",
            issue=ExtractedIssueItem(
                issue_number=1,
                title="Exposition drag",
                category="pacing",
                symptom="Too slow",
                root_cause_hypotheses=["Heavy lore"],
                evidence=[EvidenceRef(segment_ids=["S002"], note="Lore")],
                source_reader_ids=["anonymized"],
                target_audience_relevance=TargetAudienceRelevance.MEDIUM,
                minority_risk=False,
            ),
            manuscript_segments={"S002": "Paragraph 2 text"},
        )
        assert req.issue.issue_number == 1

    def test_blind_initial_ballot_forbids_tally_or_peer_votes(self) -> None:
        with pytest.raises(ValidationError):
            ReaderBlindBallotRequest(
                project_id=uuid4(),
                chapter_id=uuid4(),
                workflow_run_id=uuid4(),
                reader_profile_id="low_patience",
                issue=ExtractedIssueItem(
                    issue_number=1,
                    title="Exposition drag",
                    category="pacing",
                    symptom="Too slow",
                    root_cause_hypotheses=["Heavy lore"],
                    evidence=[EvidenceRef(segment_ids=["S002"], note="Lore")],
                    source_reader_ids=[],
                    target_audience_relevance=TargetAudienceRelevance.MEDIUM,
                    minority_risk=False,
                ),
                manuscript_segments={"S002": "Text"},
                current_tallies={"significant": 3},  # forbidden extra field
            )

    def test_valid_reader_ballot_output(self) -> None:
        out = ReaderBallotOutput(
            issue_number=1,
            severity=Severity.SIGNIFICANT,
            suggested_action=SuggestedAction.COMPRESS,
            confidence=Confidence.HIGH,
            evidence=[EvidenceRef(segment_ids=["S002"], note="Slow pacing here.")],
            reason="Takes away from the immediate urgency of the battle.",
        )
        assert out.severity == Severity.SIGNIFICANT
        assert out.suggested_action == SuggestedAction.COMPRESS


class TestDiscussionTurnAndSummaryContract:
    def test_valid_reader_discussion_turn(self) -> None:
        req = ReaderDiscussionTurnRequest(
            project_id=uuid4(),
            chapter_id=uuid4(),
            workflow_run_id=uuid4(),
            reader_profile_id="genre_experienced",
            issue=ExtractedIssueItem(
                issue_number=1,
                title="Exposition drag",
                category="pacing",
                symptom="Too slow",
                root_cause_hypotheses=["Heavy lore"],
                evidence=[EvidenceRef(segment_ids=["S002"], note="Lore")],
                source_reader_ids=[],
                target_audience_relevance=TargetAudienceRelevance.HIGH,
                minority_risk=False,
            ),
            round_number=1,
            turn_number=2,
            prior_messages=[
                {
                    "speaker_type": "reader",
                    "speaker_id": "general_immersive",
                    "stance": "support",
                    "claim": "The lore feels heavy.",
                    "evidence": [{"segment_ids": ["S002"], "note": "Lore"}],
                }
            ],
            prior_ballot={
                "severity": "minor",
                "suggested_action": "keep",
                "confidence": "medium",
            },
            manuscript_segments={"S002": "Text"},
        )
        assert req.round_number == 1

        out = ReaderDiscussionTurnOutput(
            stance=DiscussionStance.MIXED,
            claim="While the lore explains the magic system, it does pause combat momentum.",
            evidence=[EvidenceRef(segment_ids=["S002"], note="Mid-fight exposition.")],
            concession="I agree the timing is awkward, though the lore itself is accurate.",
            proposed_action="Move the explanation to after the fight concludes.",
            novelty=DiscussionNovelty.NEW_INTERPRETATION,
        )
        assert out.stance == DiscussionStance.MIXED
        assert out.novelty == DiscussionNovelty.NEW_INTERPRETATION

    def test_valid_moderator_discussion_summary(self) -> None:
        out = ModeratorDiscussionSummaryOutput(
            round_summary="Readers agree the lore is necessary but split on whether to compress or relocate it.",
            remaining_disagreements=["Whether to keep in-place with trimming or move to segment 4."],
            suggested_focus="Evaluate moving the background lore to post-combat dialogue.",
            is_consensus_reached=False,
        )
        assert out.is_consensus_reached is False


class TestFinalBallotContract:
    def test_valid_final_ballot_output(self) -> None:
        out = ReaderFinalBallotOutput(
            issue_number=1,
            severity=Severity.SIGNIFICANT,
            suggested_action=SuggestedAction.MOVE,
            confidence=Confidence.HIGH,
            evidence=[EvidenceRef(segment_ids=["S002"], note="Post-combat relocation.")],
            position_changed=True,
            change_reason="Convinced by peer that moving exposition preserves both combat tension and worldbuilding.",
            remaining_disagreement=None,
        )
        assert out.position_changed is True
        assert out.suggested_action == SuggestedAction.MOVE


class TestModeratorReportSynthesisContract:
    def test_valid_report_synthesis_request_and_output(self) -> None:
        out = ModeratorReportSynthesisOutput(
            executive_summary="Panel evaluated Chapter 1 with 4 readers. Overall willingness to continue is high (3 Yes, 1 Maybe).",
            target_audience_appeal="Strong resonance with progression fantasy readers; minor pacing drag identified in scene 2.",
            key_findings=[
                {
                    "issue_number": 1,
                    "title": "Exposition drag",
                    "consensus_class": "strong_consensus",
                    "recommended_priority": "must_fix",
                    "summary": "Mid-combat exposition slows tempo; consensus is to move or compress.",
                    "evidence": [{"segment_ids": ["S002"], "note": "Combat pause"}],
                }
            ],
            actionable_recommendations=[
                {
                    "priority": "must_fix",
                    "target_segment_ids": ["S002"],
                    "suggested_action": "move",
                    "instruction": "Relocate magic system rules explanation to immediately following the battle scene.",
                }
            ],
        )
        assert len(out.key_findings) == 1
        assert len(out.actionable_recommendations) == 1


class TestRequestBuilderHelpers:
    def test_build_cold_read_request(self) -> None:
        p_id, c_id, r_id = uuid4(), uuid4(), uuid4()
        req = build_cold_read_request(
            project_id=p_id,
            chapter_id=c_id,
            workflow_run_id=r_id,
            reader_profile_id="general_immersive",
            genre="progression_fantasy",
            target_audience=["young_adult"],
            manuscript_segments={"S001": "Opening line."},
            test_goals=["Check immersion."],
        )
        assert req.project_id == p_id
        assert req.reader_profile_id == "general_immersive"
        assert req.test_goals == ["Check immersion."]

    def test_build_blind_ballot_request_anonymizes_source_readers(self) -> None:
        p_id, c_id, r_id = uuid4(), uuid4(), uuid4()
        issue = ExtractedIssueItem(
            issue_number=1,
            title="Dialogue pacing",
            category="pacing",
            symptom="Slow dialogue",
            root_cause_hypotheses=["Exposition in dialogue"],
            evidence=[EvidenceRef(segment_ids=["S001"], note="Dialogue note")],
            source_reader_ids=["low_patience", "style_sensitive"],
            target_audience_relevance=TargetAudienceRelevance.HIGH,
            minority_risk=False,
        )
        req = build_blind_ballot_request(
            project_id=p_id,
            chapter_id=c_id,
            workflow_run_id=r_id,
            reader_profile_id="genre_experienced",
            issue=issue,
            manuscript_segments={"S001": "Dialogue text."},
        )
        assert req.issue.source_reader_ids == []
        assert req.issue.issue_number == 1

    def test_invalid_uuid_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ReaderInitialReadingRequest(
                project_id="not-a-uuid",  # type: ignore
                chapter_id=uuid4(),
                workflow_run_id=uuid4(),
                reader_profile_id="general_immersive",
                genre="fantasy",
                target_audience=[],
                manuscript_segments={"S001": "Text."},
                test_goals=[],
            )
