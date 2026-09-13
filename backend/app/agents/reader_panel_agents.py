"""Prompt templates, prompt pack builders, and agent invocation helpers for Reader Panel."""

from __future__ import annotations

from uuid import UUID

from app.agents.reader_panel_contracts import (
    ExtractedIssueItem,
    ModeratorDiscussionSummaryRequest,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisRequest,
    ReaderBlindBallotRequest,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotRequest,
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


# ---------------------------------------------------------------------------
# Prompt formatting helpers for each Reader Panel phase
# ---------------------------------------------------------------------------

def build_initial_reading_user_prompt(
    request: ReaderInitialReadingRequest,
    *,
    persona_description: str | None = None,
) -> str:
    """Constructs user prompt for isolated reader initial cold reading."""
    lines: list[str] = [
        "--- Reading Assignment Context ---",
        f"Reader Profile: {request.reader_profile_id}",
    ]
    if persona_description:
        lines.append(f"Persona Focus: {persona_description}")
    lines.extend(
        [
            f"Genre: {request.genre}",
            f"Target Audience: {', '.join(request.target_audience) if request.target_audience else 'General web novel audience'}",
        ]
    )
    if request.test_goals:
        lines.append(f"Author / Editorial Test Goals: {', '.join(request.test_goals)}")

    lines.extend(["", "--- Manuscript Segments ---"])
    for sid, text in sorted(request.manuscript_segments.items()):
        lines.append(f"[{sid}]: {text}")

    lines.extend(
        [
            "",
            "--- Evaluation Guidelines ---",
            "1. Ground all strengths, reactions, and concerns in specific segment IDs (e.g. [S001]).",
            "2. overall_reaction: Single paragraph summarizing your overall reading experience.",
            "3. continue_reading: 'yes', 'maybe', or 'no'.",
            "4. confidence: 'low', 'medium', or 'high'.",
            "5. strengths: 1 to 8 notable elements that worked well.",
            "6. reactions: key emotional or immersion beats throughout the text.",
            "7. concerns: specific friction points (pacing, logic, character, style, dialogue).",
            "Do not fabricate text segments and do not rewrite scenes.",
        ]
    )
    return "\n".join(lines)


def build_issue_extraction_user_prompt(request: ModeratorIssueExtractionRequest) -> str:
    """Constructs user prompt for neutral moderator issue extraction and deduplication."""
    lines: list[str] = [
        "--- Moderator Issue Extraction Context ---",
        f"Maximum Ballot Issues to Extract: {request.max_ballot_issues}",
        "",
        "--- Manuscript Segments Available ---",
    ]
    for sid, text in sorted(request.manuscript_segments.items()):
        lines.append(f"[{sid}]: {text}")

    lines.extend(["", "--- Reader Initial Reports ---"])
    for reader_id, report in sorted(request.reader_initial_reports.items()):
        lines.append(f"Reader [{reader_id}]:")
        lines.append(f"  Overall Reaction: {report.get('overall_reaction', 'N/A')}")
        lines.append(f"  Continue Reading: {report.get('continue_reading', 'N/A')}")
        concerns = report.get("concerns", [])
        if concerns:
            lines.append("  Concerns:")
            for c in concerns:
                cat = c.get("category", "general")
                sym = c.get("symptom", "")
                sev = c.get("severity", "minor")
                ev = c.get("evidence", [])
                sids = [sid for item in ev for sid in item.get("segment_ids", [])]
                lines.append(f"    - [{cat}] ({sev}) {sym} (segments: {sids})")
        else:
            lines.append("  Concerns: None reported.")
        lines.append("")

    lines.extend(
        [
            "--- Extraction Guidelines ---",
            "1. Deduplicate identical or overlapping reader concerns into standardized issue items.",
            "2. Disentangle reader symptoms from proposed solutions.",
            "3. Anchor every extracted issue to specific valid manuscript segment IDs.",
            "4. Flag minority high-risk issues (e.g. plot twists ruined, logic breaks) with minority_risk=true.",
            "5. REMINDER: As Moderator, you have ZERO voting authority. Do not cast votes or express preference.",
            f"6. Extract at most {request.max_ballot_issues} prioritized issues.",
        ]
    )
    return "\n".join(lines)


def build_blind_ballot_user_prompt(request: ReaderBlindBallotRequest) -> str:
    """Constructs user prompt for blind initial balloting."""
    lines: list[str] = [
        "--- Blind Initial Ballot Context ---",
        f"Reader Profile: {request.reader_profile_id}",
        "",
        "--- Issue Under Evaluation ---",
        f"Issue Number: {request.issue.issue_number}",
        f"Title: {request.issue.title}",
        f"Category: {request.issue.category}",
        f"Symptom: {request.issue.symptom}",
        f"Root Cause Hypotheses: {'; '.join(request.issue.root_cause_hypotheses)}",
        "",
        "--- Relevant Manuscript Segments ---",
    ]
    for sid, text in sorted(request.manuscript_segments.items()):
        lines.append(f"[{sid}]: {text}")

    lines.extend(
        [
            "",
            "--- Balloting Guidelines ---",
            "1. Vote completely independently without knowing who raised this issue.",
            "2. severity: 'none', 'minor', 'significant', 'critical', or 'abstain'.",
            "3. suggested_action: 'keep', 'clarify', 'compress', 'expand', 'move', 'rewrite_local', 'split', 'experiment_ab', or 'manual_review'.",
            "4. confidence: 'low', 'medium', or 'high'.",
            "5. evidence: cite 1 or more specific segment IDs supporting your vote.",
            "6. reason: concise rationale explaining why you chose this severity and action.",
        ]
    )
    return "\n".join(lines)


def build_discussion_turn_user_prompt(request: ReaderDiscussionTurnRequest) -> str:
    """Constructs user prompt for reader discussion turns."""
    lines: list[str] = [
        "--- Discussion Turn Context ---",
        f"Reader Profile: {request.reader_profile_id}",
        f"Round Number: {request.round_number}, Turn Number: {request.turn_number}",
        "",
        "--- Issue Under Discussion ---",
        f"Issue #{request.issue.issue_number}: {request.issue.title}",
        f"Category: {request.issue.category}",
        f"Symptom: {request.issue.symptom}",
    ]
    if request.prior_ballot:
        lines.extend(
            [
                "",
                "--- Your Prior Ballot ---",
                f"Severity: {request.prior_ballot.get('severity', 'N/A')}",
                f"Suggested Action: {request.prior_ballot.get('suggested_action', 'N/A')}",
                f"Reason: {request.prior_ballot.get('reason', 'N/A')}",
            ]
        )

    lines.extend(["", "--- Prior Messages in this Discussion ---"])
    if request.prior_messages:
        for msg in request.prior_messages:
            sender = msg.get("sender", "reader")
            claim = msg.get("claim") or msg.get("content") or ""
            lines.append(f"[{sender}]: {claim}")
    else:
        lines.append("(Opening turn of the discussion)")

    lines.extend(["", "--- Manuscript Segments ---"])
    for sid, text in sorted(request.manuscript_segments.items()):
        lines.append(f"[{sid}]: {text}")

    lines.extend(
        [
            "",
            "--- Guidelines ---",
            "1. stance: 'support', 'oppose', 'mixed', or 'abstain'.",
            "2. claim: state your perspective clearly with evidence citations.",
            "3. concession: if peer points are persuasive, acknowledge them; otherwise null.",
            "4. proposed_action: concrete suggested remediation action; otherwise null.",
            "5. novelty: 'new_evidence', 'new_interpretation', 'repetition', or 'procedural'.",
        ]
    )
    return "\n".join(lines)


def build_discussion_summary_user_prompt(request: ModeratorDiscussionSummaryRequest) -> str:
    """Constructs user prompt for neutral moderator discussion summaries."""
    lines: list[str] = [
        "--- Moderator Discussion Summary Context ---",
        f"Round Number: {request.round_number}",
        "",
        "--- Issue Under Discussion ---",
        f"Issue #{request.issue.issue_number}: {request.issue.title}",
        f"Symptom: {request.issue.symptom}",
        "",
        "--- Round Messages ---",
    ]
    for msg in request.round_messages:
        sender = msg.get("sender", "reader")
        claim = msg.get("claim") or msg.get("content") or ""
        lines.append(f"[{sender}]: {claim}")

    lines.extend(
        [
            "",
            "--- Moderation Guidelines ---",
            "1. round_summary: Objective, neutral summary of perspectives voiced this round.",
            "2. remaining_disagreements: List remaining genuine disagreements between readers.",
            "3. suggested_focus: Suggested focal question or resolution direction for subsequent consideration.",
            "4. is_consensus_reached: true if genuine consensus has emerged, false otherwise.",
            "5. REMINDER: Moderator has NO voting authority. Do not take sides or decide outcomes.",
        ]
    )
    return "\n".join(lines)


def build_final_ballot_user_prompt(request: ReaderFinalBallotRequest) -> str:
    """Constructs user prompt for reader final balloting post-discussion."""
    lines: list[str] = [
        "--- Final Ballot Context ---",
        f"Reader Profile: {request.reader_profile_id}",
        "",
        "--- Issue Under Final Evaluation ---",
        f"Issue #{request.issue.issue_number}: {request.issue.title}",
        f"Symptom: {request.issue.symptom}",
    ]
    if request.initial_ballot:
        lines.extend(
            [
                "",
                "--- Your Initial Ballot ---",
                f"Initial Severity: {request.initial_ballot.get('severity', 'N/A')}",
                f"Initial Action: {request.initial_ballot.get('suggested_action', 'N/A')}",
                f"Initial Reason: {request.initial_ballot.get('reason', 'N/A')}",
            ]
        )
    if request.round_summaries:
        lines.extend(["", "--- Group Discussion Round Summaries ---"])
        for idx, s in enumerate(request.round_summaries, 1):
            lines.append(f"Round {idx}: {s}")

    lines.extend(["", "--- Manuscript Segments ---"])
    for sid, text in sorted(request.manuscript_segments.items()):
        lines.append(f"[{sid}]: {text}")

    lines.extend(
        [
            "",
            "--- Final Ballot Guidelines ---",
            "1. Cast your final independent vote on severity, suggested_action, and confidence.",
            "2. position_changed: Set true if your view shifted compared to your initial ballot, false otherwise.",
            "3. change_reason: If position_changed is true, explain what persuaded you; otherwise null.",
            "4. remaining_disagreement: Any residual reservation or minority concern; otherwise null.",
        ]
    )
    return "\n".join(lines)


def build_report_synthesis_user_prompt(request: ModeratorReportSynthesisRequest) -> str:
    """Constructs user prompt for moderator final report synthesis."""
    lines: list[str] = [
        "--- Moderator Report Synthesis Context ---",
        "",
        "--- Initial Reader Reports Summary ---",
    ]
    for rid, rep in sorted(request.initial_reports.items()):
        lines.append(f"Reader [{rid}]:")
        lines.append(f"  Reaction: {rep.get('overall_reaction', 'N/A')}")
        lines.append(f"  Continue: {rep.get('continue_reading', 'N/A')}")

    lines.extend(["", "--- Extracted Issues & Final Consensus (Server-Computed) ---"])
    for issue in request.extracted_issues:
        consensus = request.final_consensus_results.get(issue.issue_number, {})
        is_minority_risk = issue.issue_number in request.minority_risk_issues or issue.minority_risk
        lines.append(f"Issue #{issue.issue_number}: {issue.title}")
        lines.append(f"  Category: {issue.category}, Symptom: {issue.symptom}")
        lines.append(f"  Server Consensus Class: {consensus.get('consensus_class', 'inconclusive')}")
        lines.append(f"  Recommended Priority: {consensus.get('recommended_priority', 'manual_review')}")
        lines.append(f"  Suggested Action: {consensus.get('suggested_action', 'manual_review')}")
        lines.append(f"  Minority High-Risk: {is_minority_risk}")
        ev_sids = [sid for e in issue.evidence for sid in e.segment_ids]
        lines.append(f"  Target Segments: {ev_sids}")
        lines.append("")

    lines.extend(
        [
            "--- Synthesis Guidelines ---",
            "1. executive_summary: Concise overview of reader reception, continuation willingness, and key dynamics.",
            "2. target_audience_appeal: Assessment of resonance with the target reader demographic.",
            "3. key_findings: Synthesize each evaluated issue with its server consensus class, recommended priority, and evidence.",
            "4. actionable_recommendations: Concrete, prioritized recommendations citing target segment IDs and suggested actions.",
            "5. REMINDER: Moderator provides objective diagnostic reporting for the author/editor. Moderator DOES NOT modify manuscript text or approve chapters.",
        ]
    )
    return "\n".join(lines)
