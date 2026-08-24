"""Unit tests for fail-closed Reader Panel state machine, modes, and decisions."""

from __future__ import annotations

from dataclasses import replace
import pytest

from app.workflows.reader_panel import (
    BallotVote,
    ConsensusClass,
    PanelMode,
    ReaderPanelState,
    ReaderPanelStatus,
    ReaderPanelValidationError,
    RiskFlag,
    Severity,
    SuggestedAction,
    classify_issue_consensus,
    get_mode_preset_config,
    is_mode_off,
)


class TestReaderPanelModes:
    def test_presets_snapshot_complete_hard_budgets(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)

        assert config.max_total_model_calls > 0
        assert config.max_model_calls_per_phase > 0
        assert config.max_total_input_tokens > 0
        assert config.max_total_output_tokens > 0
        assert config.max_input_tokens_per_call > 0
        assert config.max_output_tokens_per_call > 0
        assert config.max_messages > 0
        assert config.max_provider_attempts >= 1
        assert config.max_invalid_output_repairs == 1

    def test_mode_off_preset_is_noop(self) -> None:
        config = get_mode_preset_config(PanelMode.OFF)
        assert config.mode == PanelMode.OFF
        assert is_mode_off(config.mode) is True
        assert config.reader_count == 0
        assert config.min_valid_readers == 0
        assert config.max_ballot_issues == 0
        assert config.max_discussion_issues == 0
        assert config.max_rounds_per_issue == 0

    def test_quick_mode_preset_defaults(self) -> None:
        config = get_mode_preset_config(PanelMode.QUICK)
        assert config.mode == PanelMode.QUICK
        assert is_mode_off(config.mode) is False
        assert config.reader_count == 2
        assert config.min_valid_readers == 2
        assert config.max_ballot_issues == 3
        assert config.max_discussion_issues == 2
        assert config.max_rounds_per_issue == 1
        assert len(config.reader_profile_ids) == 2
        assert "general_immersive" in config.reader_profile_ids
        assert "low_patience" in config.reader_profile_ids

    def test_standard_mode_preset_defaults(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        assert config.mode == PanelMode.STANDARD
        assert config.reader_count == 4
        assert config.min_valid_readers == 3
        assert config.max_ballot_issues == 6
        assert config.max_discussion_issues == 4
        assert config.max_rounds_per_issue == 2
        assert len(config.reader_profile_ids) == 4

    def test_panel_mode_preset_defaults(self) -> None:
        config = get_mode_preset_config(PanelMode.PANEL)
        assert config.mode == PanelMode.PANEL
        assert config.reader_count == 6
        assert config.min_valid_readers == 4
        assert config.max_ballot_issues == 8
        assert config.max_discussion_issues == 6
        assert config.max_rounds_per_issue == 3
        assert len(config.reader_profile_ids) == 6


class TestReaderPanelTransitions:
    def test_initial_state_creation(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        )
        assert state.status == ReaderPanelStatus.CREATED
        assert state.stale is False
        assert state.session_id == "11111111-1111-4111-8111-111111111111"
        assert state.is_terminal is False

    def test_happy_path_lifecycle_transitions(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        )

        state = state.transition_to(ReaderPanelStatus.PREPARING)
        assert state.status == ReaderPanelStatus.PREPARING

        state = state.transition_to(ReaderPanelStatus.INDEPENDENT_READING)
        assert state.status == ReaderPanelStatus.INDEPENDENT_READING

        state = state.transition_to(ReaderPanelStatus.INITIAL_REPORTS_LOCKED)
        assert state.status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED
        assert state.initial_reports_locked is True

        state = state.transition_to(ReaderPanelStatus.ISSUE_EXTRACTION)
        assert state.status == ReaderPanelStatus.ISSUE_EXTRACTION

        state = state.transition_to(ReaderPanelStatus.INITIAL_BALLOTING)
        assert state.status == ReaderPanelStatus.INITIAL_BALLOTING

        state = state.transition_to(ReaderPanelStatus.INITIAL_BALLOTS_LOCKED)
        assert state.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED
        assert state.initial_ballots_locked is True

        state = state.transition_to(ReaderPanelStatus.DISCUSSING)
        assert state.status == ReaderPanelStatus.DISCUSSING

        state = state.transition_to(ReaderPanelStatus.FINAL_BALLOTING)
        assert state.status == ReaderPanelStatus.FINAL_BALLOTING

        state = state.transition_to(ReaderPanelStatus.FINAL_BALLOTS_LOCKED)
        assert state.status == ReaderPanelStatus.FINAL_BALLOTS_LOCKED
        assert state.final_ballots_locked is True

        state = state.transition_to(ReaderPanelStatus.REPORT_GENERATING)
        assert state.status == ReaderPanelStatus.REPORT_GENERATING

        state = state.transition_to(ReaderPanelStatus.COMPLETED)
        assert state.status == ReaderPanelStatus.COMPLETED
        assert state.is_terminal is True

    def test_unlisted_transition_fails_closed(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        )

        with pytest.raises(ReaderPanelValidationError, match="Illegal state transition"):
            state.transition_to(ReaderPanelStatus.COMPLETED)

        with pytest.raises(ReaderPanelValidationError, match="Illegal state transition"):
            state.transition_to(ReaderPanelStatus.DISCUSSING)

    def test_terminal_state_cannot_transition(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        ).transition_to(ReaderPanelStatus.CANCELLED)

        assert state.is_terminal is True
        with pytest.raises(ReaderPanelValidationError, match="Terminal state cannot transition"):
            state.transition_to(ReaderPanelStatus.PREPARING)

    def test_stale_flag_is_orthogonal_to_status(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        )
        assert state.stale is False

        stale_state = state.mark_stale()
        assert stale_state.stale is True
        assert stale_state.status == ReaderPanelStatus.CREATED

        advanced = stale_state.transition_to(ReaderPanelStatus.PREPARING)
        assert advanced.stale is True
        assert advanced.status == ReaderPanelStatus.PREPARING


class TestDeterministicConsensusClassification:
    def test_strong_consensus_when_75_percent_significant_or_critical(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1",
                severity=Severity.CRITICAL,
                suggested_action=SuggestedAction.REWRITE_LOCAL,
            ),
            BallotVote(
                reader_id="r2",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.COMPRESS,
            ),
            BallotVote(
                reader_id="r3",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.CLARIFY,
            ),
            BallotVote(
                reader_id="r4", severity=Severity.MINOR, suggested_action=SuggestedAction.KEEP
            ),
        ]
        result = classify_issue_consensus(votes)
        assert result.consensus_class == ConsensusClass.STRONG_CONSENSUS
        assert result.total_votes == 4
        assert result.valid_votes == 4
        assert result.severity_distribution[Severity.CRITICAL] == 1
        assert result.severity_distribution[Severity.SIGNIFICANT] == 2
        assert result.severity_distribution[Severity.MINOR] == 1
        assert result.recommended_priority == "must_fix"

    def test_weak_consensus_when_majority_agree_issue_exists_with_low_dispersion(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.COMPRESS,
            ),
            BallotVote(
                reader_id="r2", severity=Severity.MINOR, suggested_action=SuggestedAction.CLARIFY
            ),
            BallotVote(
                reader_id="r3", severity=Severity.MINOR, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r4", severity=Severity.MINOR, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r5", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
        ]
        # 4/5 = 80% agree issue exists (1 sig, 3 minor, 1 none). Sig/crit is 20% (<30% so not polarized).
        result = classify_issue_consensus(votes)
        assert result.consensus_class == ConsensusClass.WEAK_CONSENSUS
        assert result.recommended_priority == "manual_review"

    def test_polarized_when_30_percent_low_and_30_percent_high(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.COMPRESS,
            ),
            BallotVote(
                reader_id="r2", severity=Severity.CRITICAL, suggested_action=SuggestedAction.MOVE
            ),
            BallotVote(
                reader_id="r3", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r4", severity=Severity.MINOR, suggested_action=SuggestedAction.KEEP
            ),
        ]
        # High: 2/4 = 50% (>= 30%), Low: 2/4 = 50% (>= 30%)
        result = classify_issue_consensus(votes)
        assert result.consensus_class == ConsensusClass.POLARIZED
        assert result.recommended_priority == "experiment"

    def test_accepted_when_majority_rates_none_or_minor(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r2", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r3", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r4", severity=Severity.MINOR, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r5",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.CLARIFY,
            ),
        ]
        result = classify_issue_consensus(votes)
        assert result.consensus_class == ConsensusClass.ACCEPTED
        assert result.recommended_priority == "keep"

    def test_minority_high_risk_flag_triggered_independently(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r2", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r3", severity=Severity.NONE, suggested_action=SuggestedAction.KEEP
            ),
            BallotVote(
                reader_id="r4",
                severity=Severity.CRITICAL,
                suggested_action=SuggestedAction.REWRITE_LOCAL,
                confidence="high",
                has_fatal_risk=True,
            ),
        ]
        result = classify_issue_consensus(votes)
        assert RiskFlag.MINORITY_HIGH_RISK in result.risk_flags
        # Even if majority is accepted, minority high risk elevates priority
        assert result.recommended_priority == "must_fix"

    def test_target_audience_distribution_separation(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.COMPRESS,
                is_target_audience=True,
            ),
            BallotVote(
                reader_id="r2",
                severity=Severity.SIGNIFICANT,
                suggested_action=SuggestedAction.COMPRESS,
                is_target_audience=True,
            ),
            BallotVote(
                reader_id="r3",
                severity=Severity.NONE,
                suggested_action=SuggestedAction.KEEP,
                is_target_audience=False,
            ),
            BallotVote(
                reader_id="r4",
                severity=Severity.NONE,
                suggested_action=SuggestedAction.KEEP,
                is_target_audience=False,
            ),
        ]
        result = classify_issue_consensus(votes)
        assert result.target_audience_votes == 2
        assert result.target_audience_distribution[Severity.SIGNIFICANT] == 2
        assert result.target_audience_distribution[Severity.NONE] == 0


class TestReaderPanelCheckpoint:
    def test_checkpoint_roundtrips_every_hard_budget_without_defaulting(self) -> None:
        config = replace(
            get_mode_preset_config(PanelMode.QUICK),
            max_total_model_calls=7,
            max_model_calls_per_phase=3,
            max_total_input_tokens=101,
            max_total_output_tokens=103,
            max_input_tokens_per_call=17,
            max_output_tokens_per_call=19,
            max_messages=5,
            max_provider_attempts=2,
            max_invalid_output_repairs=1,
            max_execution_seconds=23,
        )
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        )

        restored = ReaderPanelState.from_checkpoint_dict(state.to_checkpoint_dict())

        assert restored.config == config

    def test_checkpoint_rejects_bool_or_missing_hard_budget(self) -> None:
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-833333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=get_mode_preset_config(PanelMode.QUICK),
        )
        checkpoint = state.to_checkpoint_dict()
        checkpoint["config"]["max_total_output_tokens"] = True
        with pytest.raises(ReaderPanelValidationError):
            ReaderPanelState.from_checkpoint_dict(checkpoint)

        checkpoint = state.to_checkpoint_dict()
        del checkpoint["config"]["max_model_calls_per_phase"]
        with pytest.raises(ReaderPanelValidationError):
            ReaderPanelState.from_checkpoint_dict(checkpoint)

    def test_checkpoint_roundtrip_is_content_free(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = ReaderPanelState.create(
            session_id="11111111-1111-4111-8111-111111111111",
            project_id="22222222-2222-4222-8222-222222222222",
            document_id="33333333-3333-4333-8333-333333333333",
            document_version_id="44444444-4444-4444-8444-444444444444",
            source_hash="a" * 64,
            config=config,
        ).transition_to(ReaderPanelStatus.PREPARING)

        checkpoint_data = state.to_checkpoint_dict()
        assert "content" not in checkpoint_data
        assert "manuscript" not in checkpoint_data
        assert "prompt" not in checkpoint_data
        assert "discussion_text" not in checkpoint_data

        restored_state = ReaderPanelState.from_checkpoint_dict(checkpoint_data)
        assert restored_state.session_id == state.session_id
        assert restored_state.status == ReaderPanelStatus.PREPARING
        assert restored_state.config.mode == PanelMode.STANDARD
        assert restored_state.source_hash == "a" * 64

    def test_corrupted_checkpoint_fails_closed(self) -> None:
        with pytest.raises(ReaderPanelValidationError, match="Invalid checkpoint"):
            ReaderPanelState.from_checkpoint_dict({"invalid": "data"})


class TestReaderPanelAdvancedEdgeCases:
    def test_zero_extracted_issues_skips_balloting_to_report_generating(self) -> None:
        config = get_mode_preset_config(PanelMode.QUICK)
        state = (
            ReaderPanelState.create(
                session_id="11111111-1111-4111-8111-111111111111",
                project_id="22222222-2222-4222-8222-222222222222",
                document_id="33333333-3333-4333-8333-333333333333",
                document_version_id="44444444-4444-4444-8444-444444444444",
                source_hash="a" * 64,
                config=config,
            )
            .transition_to(ReaderPanelStatus.PREPARING)
            .transition_to(ReaderPanelStatus.INDEPENDENT_READING)
            .transition_to(ReaderPanelStatus.INITIAL_REPORTS_LOCKED)
            .transition_to(ReaderPanelStatus.ISSUE_EXTRACTION)
        )
        # When 0 issues are extracted, orchestrator can directly jump to REPORT_GENERATING
        state = state.transition_to(ReaderPanelStatus.REPORT_GENERATING)
        assert state.status == ReaderPanelStatus.REPORT_GENERATING

    def test_zero_discussion_issues_skips_discussing_to_final_balloting(self) -> None:
        config = get_mode_preset_config(PanelMode.QUICK)
        state = (
            ReaderPanelState.create(
                session_id="11111111-1111-4111-8111-111111111111",
                project_id="22222222-2222-4222-8222-222222222222",
                document_id="33333333-3333-4333-8333-333333333333",
                document_version_id="44444444-4444-4444-8444-444444444444",
                source_hash="a" * 64,
                config=config,
            )
            .transition_to(ReaderPanelStatus.PREPARING)
            .transition_to(ReaderPanelStatus.INDEPENDENT_READING)
            .transition_to(ReaderPanelStatus.INITIAL_REPORTS_LOCKED)
            .transition_to(ReaderPanelStatus.ISSUE_EXTRACTION)
            .transition_to(ReaderPanelStatus.INITIAL_BALLOTING)
            .transition_to(ReaderPanelStatus.INITIAL_BALLOTS_LOCKED)
        )
        # When no issues require group discussion, transition directly to FINAL_BALLOTING
        state = state.transition_to(ReaderPanelStatus.FINAL_BALLOTING)
        assert state.status == ReaderPanelStatus.FINAL_BALLOTING

    def test_degraded_completed_transition(self) -> None:
        config = get_mode_preset_config(PanelMode.STANDARD)
        state = (
            ReaderPanelState.create(
                session_id="11111111-1111-4111-8111-111111111111",
                project_id="22222222-2222-4222-8222-222222222222",
                document_id="33333333-3333-4333-8333-333333333333",
                document_version_id="44444444-4444-4444-8444-444444444444",
                source_hash="a" * 64,
                config=config,
            )
            .transition_to(ReaderPanelStatus.PREPARING)
            .transition_to(ReaderPanelStatus.INDEPENDENT_READING)
            .transition_to(ReaderPanelStatus.INITIAL_REPORTS_LOCKED)
            .transition_to(ReaderPanelStatus.ISSUE_EXTRACTION)
            .transition_to(ReaderPanelStatus.INITIAL_BALLOTING)
            .transition_to(ReaderPanelStatus.INITIAL_BALLOTS_LOCKED)
            .transition_to(ReaderPanelStatus.DISCUSSING)
            .transition_to(ReaderPanelStatus.FINAL_BALLOTING)
            .transition_to(ReaderPanelStatus.FINAL_BALLOTS_LOCKED)
            .transition_to(ReaderPanelStatus.REPORT_GENERATING)
            .transition_to(ReaderPanelStatus.DEGRADED_COMPLETED)
        )
        assert state.status == ReaderPanelStatus.DEGRADED_COMPLETED
        assert state.is_terminal is True

    def test_all_abstain_yields_inconclusive(self) -> None:
        votes = [
            BallotVote(
                reader_id="r1",
                severity=Severity.ABSTAIN,
                suggested_action=SuggestedAction.MANUAL_REVIEW,
            ),
            BallotVote(
                reader_id="r2",
                severity=Severity.ABSTAIN,
                suggested_action=SuggestedAction.MANUAL_REVIEW,
            ),
        ]
        result = classify_issue_consensus(votes)
        assert result.consensus_class == ConsensusClass.INCONCLUSIVE
        assert result.valid_votes == 0
        assert result.total_votes == 2
        assert result.recommended_priority == "manual_review"

    def test_invalid_uuid_fails_closed(self) -> None:
        config = get_mode_preset_config(PanelMode.QUICK)
        with pytest.raises(ReaderPanelValidationError, match="valid canonical UUID"):
            ReaderPanelState.create(
                session_id="not-a-valid-uuid",
                project_id="22222222-2222-4222-8222-222222222222",
                document_id="33333333-3333-4333-8333-333333333333",
                document_version_id="44444444-4444-4444-8444-444444444444",
                source_hash="a" * 64,
                config=config,
            )

    def test_invalid_hash_fails_closed(self) -> None:
        config = get_mode_preset_config(PanelMode.QUICK)
        with pytest.raises(ReaderPanelValidationError, match="64-character hex"):
            ReaderPanelState.create(
                session_id="11111111-1111-4111-8111-111111111111",
                project_id="22222222-2222-4222-8222-222222222222",
                document_id="33333333-3333-4333-8333-333333333333",
                document_version_id="44444444-4444-4444-8444-444444444444",
                source_hash="short_invalid_hash",
                config=config,
            )
