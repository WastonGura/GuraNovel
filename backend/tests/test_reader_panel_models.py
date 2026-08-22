"""Unit tests for Reader Panel database models and schema definitions."""

from __future__ import annotations

from sqlalchemy import inspect

from app.models.core import Chapter, Project, WorkflowRun
from app.models.enums import WorkflowType
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
)


class TestReaderPanelModelStructure:
    def test_workflow_type_includes_reader_panel(self) -> None:
        assert WorkflowType.READER_PANEL == "reader_panel"

    def test_reader_panel_session_table_schema(self) -> None:
        assert ReaderPanelSession.__tablename__ == "reader_panel_sessions"
        mapper = inspect(ReaderPanelSession)

        assert "id" in mapper.columns
        assert "project_id" in mapper.columns
        assert "chapter_id" in mapper.columns
        assert "workflow_run_id" in mapper.columns
        assert "document_id" in mapper.columns
        assert "document_version_id" in mapper.columns
        assert "source_hash" in mapper.columns
        assert "mode" in mapper.columns
        assert "status" in mapper.columns
        assert "stale" in mapper.columns
        assert "config_snapshot" in mapper.columns
        assert "model_snapshot" in mapper.columns
        assert "prompt_snapshot" in mapper.columns
        assert "target_audience" in mapper.columns
        assert "test_goals" in mapper.columns
        assert "step_counter" in mapper.columns
        assert "current_step" in mapper.columns
        assert "degradation_reason" in mapper.columns
        assert "failure_reason" in mapper.columns
        assert "initial_reports_locked_at" in mapper.columns
        assert "initial_ballots_locked_at" in mapper.columns
        assert "final_ballots_locked_at" in mapper.columns
        assert "completed_at" in mapper.columns
        assert "review_report_id" in mapper.columns

    def test_reader_run_table_schema(self) -> None:
        assert ReaderRun.__tablename__ == "reader_runs"
        mapper = inspect(ReaderRun)

        assert "id" in mapper.columns
        assert "session_id" in mapper.columns
        assert "reader_profile_id" in mapper.columns
        assert "status" in mapper.columns
        assert "is_target_audience" in mapper.columns
        assert "retry_count" in mapper.columns
        assert "error_code" in mapper.columns
        assert "error_message" in mapper.columns
        assert "completed_at" in mapper.columns

    def test_reader_initial_report_table_schema(self) -> None:
        assert ReaderInitialReport.__tablename__ == "reader_initial_reports"
        mapper = inspect(ReaderInitialReport)

        assert "id" in mapper.columns
        assert "reader_run_id" in mapper.columns
        assert "session_id" in mapper.columns
        assert "overall_reaction" in mapper.columns
        assert "continue_reading" in mapper.columns
        assert "confidence" in mapper.columns
        assert "strengths" in mapper.columns
        assert "reactions" in mapper.columns
        assert "concerns" in mapper.columns
        assert "locked" in mapper.columns
        assert "locked_at" in mapper.columns

    def test_reader_panel_issue_table_schema(self) -> None:
        assert ReaderPanelIssue.__tablename__ == "reader_panel_issues"
        mapper = inspect(ReaderPanelIssue)

        assert "id" in mapper.columns
        assert "session_id" in mapper.columns
        assert "issue_number" in mapper.columns
        assert "title" in mapper.columns
        assert "category" in mapper.columns
        assert "symptom" in mapper.columns
        assert "root_cause_hypotheses" in mapper.columns
        assert "evidence" in mapper.columns
        assert "source_reader_ids" in mapper.columns
        assert "target_audience_relevance" in mapper.columns
        assert "minority_risk" in mapper.columns
        assert "discussion_status" in mapper.columns
        assert "consensus_class" in mapper.columns
        assert "recommended_priority" in mapper.columns
        assert "final_tally" in mapper.columns

    def test_reader_panel_ballot_table_schema(self) -> None:
        assert ReaderPanelBallot.__tablename__ == "reader_panel_ballots"
        mapper = inspect(ReaderPanelBallot)

        assert "id" in mapper.columns
        assert "session_id" in mapper.columns
        assert "reader_run_id" in mapper.columns
        assert "issue_id" in mapper.columns
        assert "phase" in mapper.columns
        assert "severity" in mapper.columns
        assert "suggested_action" in mapper.columns
        assert "confidence" in mapper.columns
        assert "evidence" in mapper.columns
        assert "position_changed" in mapper.columns
        assert "change_reason" in mapper.columns
        assert "remaining_disagreement" in mapper.columns

    def test_reader_panel_message_table_schema(self) -> None:
        assert ReaderPanelMessage.__tablename__ == "reader_panel_messages"
        mapper = inspect(ReaderPanelMessage)

        assert "id" in mapper.columns
        assert "session_id" in mapper.columns
        assert "issue_id" in mapper.columns
        assert "round_number" in mapper.columns
        assert "turn_number" in mapper.columns
        assert "speaker_type" in mapper.columns
        assert "reader_run_id" in mapper.columns
        assert "stance" in mapper.columns
        assert "claim" in mapper.columns
        assert "evidence" in mapper.columns
        assert "concession" in mapper.columns
        assert "proposed_action" in mapper.columns
        assert "novelty" in mapper.columns
        assert "idempotency_key" in mapper.columns


class TestReaderPanelModelRelationships:
    def test_project_and_chapter_relationships(self) -> None:
        proj_mapper = inspect(Project)
        assert "reader_panel_sessions" in proj_mapper.relationships
        assert proj_mapper.relationships["reader_panel_sessions"].target.name == "reader_panel_sessions"

        chap_mapper = inspect(Chapter)
        assert "reader_panel_sessions" in chap_mapper.relationships
        assert chap_mapper.relationships["reader_panel_sessions"].target.name == "reader_panel_sessions"

        wf_mapper = inspect(WorkflowRun)
        assert "reader_panel_session" in wf_mapper.relationships
        assert wf_mapper.relationships["reader_panel_session"].target.name == "reader_panel_sessions"

    def test_session_child_relationships(self) -> None:
        session_mapper = inspect(ReaderPanelSession)
        assert "reader_runs" in session_mapper.relationships
        assert "initial_reports" in session_mapper.relationships
        assert "issues" in session_mapper.relationships
        assert "ballots" in session_mapper.relationships
        assert "messages" in session_mapper.relationships
        assert "project" in session_mapper.relationships
        assert "chapter" in session_mapper.relationships
        assert "workflow_run" in session_mapper.relationships
        assert "document" in session_mapper.relationships
        assert "document_version" in session_mapper.relationships

    def test_reader_run_relationships(self) -> None:
        run_mapper = inspect(ReaderRun)
        assert "session" in run_mapper.relationships
        assert "initial_report" in run_mapper.relationships
        assert "ballots" in run_mapper.relationships
        assert "messages" in run_mapper.relationships

    def test_reader_panel_issue_relationships(self) -> None:
        issue_mapper = inspect(ReaderPanelIssue)
        assert "session" in issue_mapper.relationships
        assert "ballots" in issue_mapper.relationships
        assert "messages" in issue_mapper.relationships

    def test_reader_panel_ballot_and_message_relationships(self) -> None:
        ballot_mapper = inspect(ReaderPanelBallot)
        assert "session" in ballot_mapper.relationships
        assert "reader_run" in ballot_mapper.relationships
        assert "issue" in ballot_mapper.relationships

        msg_mapper = inspect(ReaderPanelMessage)
        assert "session" in msg_mapper.relationships
        assert "reader_run" in msg_mapper.relationships
        assert "issue" in msg_mapper.relationships
