"""Prompt templates, prompt pack builders, and agent invocation helpers for Reader Panel."""

from __future__ import annotations

from uuid import UUID

from app.agents.reader_panel_contracts import (
    ExtractedIssueItem,
    ReaderBlindBallotRequest,
    ReaderInitialReadingRequest,
)


READER_INITIAL_READING_SYSTEM_PROMPT = """You are a dedicated reader evaluating a web novel chapter in complete isolation.
Read the provided manuscript text and provide your initial reading impressions.
Follow these rules strictly:
1. Ground every strength, reaction, and concern in explicit text segment identifiers (e.g. [S001]).
2. Evaluate honestly according to your reader persona orientation without behaving like a punitive auditor.
3. State your overall willingness to continue reading ('yes', 'maybe', 'no') with confidence level.
4. Do not rewrite whole scenes or speculate beyond the provided chapter text.
5. Return structured JSON matching the requested schema.
"""

MODERATOR_ISSUE_EXTRACTION_SYSTEM_PROMPT = """You are the neutral discussion moderator for a web novel reader panel.
Your task is to extract, deduplicate, and standardize issues from initial reader reports.
Follow these rules strictly:
1. Combine semantically and locationally identical concerns across readers into a single issue.
2. Disentangle reader symptoms from proposed solutions.
3. Anchor all extracted issues to specific text segment IDs cited in the reports.
4. Identify minority high-risk signals (e.g., plot holes, premature twist unmasking, severe drop-off risks).
5. You have NO voting authority. Do not cast votes or express personal preference.
6. Return structured JSON matching the requested schema.
"""

READER_BLIND_BALLOT_SYSTEM_PROMPT = """You are a reader casting a blind initial ballot on a standardized story issue.
Evaluate the issue neutrally based only on the issue description and relevant manuscript segments.
Follow these rules strictly:
1. Vote independently without regard to who raised the issue.
2. Assign a severity rating ('none', 'minor', 'significant', 'critical', 'abstain').
3. Recommend a remediation action ('keep', 'clarify', 'compress', 'expand', 'move', 'rewrite_local', 'split', 'experiment_ab', 'manual_review').
4. Cite specific segment IDs as evidence for your vote.
5. Return structured JSON matching the requested schema.
"""

READER_DISCUSSION_TURN_SYSTEM_PROMPT = """You are a reader participating in a structured discussion round on a specific story issue.
Express your perspective and engage constructively with previous reader messages.
Follow these rules strictly:
1. State your stance ('support', 'oppose', 'mixed', 'abstain') on the issue.
2. Make a concise claim supported by text evidence citations.
3. If presented with convincing peer arguments, make clear concessions or propose refined remediation actions.
4. Keep remarks focused strictly on the current issue without personal attacks.
5. Return structured JSON matching the requested schema.
"""

MODERATOR_DISCUSSION_SUMMARY_SYSTEM_PROMPT = """You are the neutral discussion moderator summarizing a discussion round on an issue.
Follow these rules strictly:
1. Provide an objective, neutral summary of the discussion turn.
2. Highlight remaining genuine disagreements between readers.
3. Suggest a clear focus for subsequent consideration without taking sides or voting.
4. Return structured JSON matching the requested schema.
"""

READER_FINAL_BALLOT_SYSTEM_PROMPT = """You are a reader casting your final ballot after participating in group discussion.
Follow these rules strictly:
1. Cast your final independent evaluation on severity, suggested action, and confidence.
2. State clearly whether your position changed from your initial ballot (`position_changed`).
3. If changed, cite the reason and arguments that persuaded you.
4. Note any remaining residual disagreements.
5. Return structured JSON matching the requested schema.
"""

MODERATOR_REPORT_SYNTHESIS_SYSTEM_PROMPT = """You are the neutral panel moderator synthesizing the final reader panel evaluation report.
Follow these rules strictly:
1. Summarize overall reader continuation willingness and target audience resonance.
2. Synthesize key findings across strong consensus, weak consensus, polarized topics, and minority high-risk issues.
3. Formulate actionable revision recommendations with concrete target segment IDs and suggested actions.
4. You cannot vote or decide authorial revisions directly; provide objective diagnostic evidence for the editor.
5. Return structured JSON matching the requested schema.
"""


def build_cold_read_request(
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    reader_profile_id: str,
    genre: str,
    target_audience: list[str],
    manuscript_segments: dict[str, str],
    test_goals: list[str] | None = None,
) -> ReaderInitialReadingRequest:
    """Builds a strictly isolated cold-reading request with no peer information."""
    return ReaderInitialReadingRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=workflow_run_id,
        reader_profile_id=reader_profile_id,
        genre=genre,
        target_audience=target_audience,
        manuscript_segments=manuscript_segments,
        test_goals=test_goals or [],
    )


def build_blind_ballot_request(
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    reader_profile_id: str,
    issue: ExtractedIssueItem,
    manuscript_segments: dict[str, str],
) -> ReaderBlindBallotRequest:
    """Builds a blind initial ballot request masking issue originators and tallies."""
    # Anonymize source reader IDs to ensure blind evaluation
    blind_issue = issue.model_copy(update={"source_reader_ids": []})
    return ReaderBlindBallotRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=workflow_run_id,
        reader_profile_id=reader_profile_id,
        issue=blind_issue,
        manuscript_segments=manuscript_segments,
    )
