"""Comprehensive tests for OpenAI-compatible chapter writing and review providers."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

import app.api.deps as deps_module
from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    ApprovedOutlineSnapshot,
    CandidateChapterOutput,
    ChapterReviewFinding,
    ChapterReviewReport,
    ReviewSegmentSnapshot,
    ChapterReviewTarget,
    ChiefEditorChapterFinalAgent,
    ChiefEditorChapterFinalRequest,
    DeterministicChapterReviewProvider,
    DeterministicChapterWriterProvider,
    EditorAgent,
    EditorReviewRequest,
    InitialDraftRequest,
    LoreChapterFinalAgent,
    LoreChapterFinalRequest,
    OpenAICompatibleChapterReviewProvider,
    OpenAICompatibleChapterWriterProvider,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewDrivenRevisionRequest,
    ReviewFindingSeverity,
    ReviewReportReference,
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
from app.api.deps import get_chapter_production_v2_composition
from app.core.config import Settings
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "chapter_generation_provider": "fake",
        "chapter_production_provider": "fake",
        "reader_panel_mode": "off",
        "openai_compatible_base_url": None,
        "openai_compatible_api_key": None,
        "openai_compatible_model": None,
        "openai_compatible_timeout_seconds": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _make_sample_candidate_payload(
    request: Any,
    *,
    text_prefix: str = "Candidate text for",
) -> dict[str, Any]:
    source_draft = getattr(request, "source_draft", None)
    target_ids = set(
        getattr(request, "target_segment_ids", (s.segment_id for s in request.allowed_segments))
    )
    return {
        "project_id": str(request.project_id),
        "chapter_id": str(request.chapter_id),
        "workflow_run_id": str(request.workflow_run_id),
        "approved_outline_document_id": str(request.approved_outline.document_id),
        "approved_outline_version_id": str(request.approved_outline.version_id),
        "source_draft_document_id": (
            str(source_draft.document_id) if source_draft is not None else None
        ),
        "source_draft_version_id": (
            str(source_draft.version_id) if source_draft is not None else None
        ),
        "complete_chapter": isinstance(request, InitialDraftRequest),
        "segments": [
            {
                "segment_id": str(seg.segment_id),
                "index": seg.index,
                "title": seg.title,
                "content": f"{text_prefix} segment {seg.index}: {seg.title}",
            }
            for seg in request.allowed_segments
            if seg.segment_id in target_ids
        ],
        "summary": "Generated chapter candidate conforming to approved outline beats.",
        "self_check": {
            "outline_followed": True,
            "allowed_segments_only": True,
            "continuity_checked": True,
            "notes": ["All beats verified against Victorian gothic style constraints."],
        },
        "uncertainty_markers": [],
    }


def _make_sample_review_payload(
    request: Any,
    *,
    passed: bool = True,
    reviewer_role: str,
    review_mode: str,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if not passed:
        findings.append(
            {
                "sequence": 1,
                "code": "blocking_pacing_collapse",
                "severity": "blocking",
                "required": True,
                "evidence_segment_ids": [str(request.target.segments[0].segment_id)],
                "rationale": "Severe motivation lapse identified in scene opening.",
                "suggested_action": "Reinstate detective's core internal desire before discovery.",
            }
        )
    return {
        "project_id": str(request.project_id),
        "chapter_id": str(request.chapter_id),
        "workflow_run_id": str(request.workflow_run_id),
        "reviewer_role": reviewer_role,
        "review_mode": review_mode,
        "target_document_id": str(request.target.document_id),
        "target_version_id": str(request.target.version_id),
        "passed": passed,
        "summary": "Thorough editorial review completed against established criteria.",
        "findings": findings,
        "suggested_actions": ["Review scene beats."] if not passed else [],
    }


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def _make_mock_client(
    handler_or_payload: Any,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    recorded_requests: list[dict[str, Any]] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        recorded_requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": body,
            }
        )
        if callable(handler_or_payload):
            return handler_or_payload(request, body)

        if isinstance(handler_or_payload, Exception):
            raise handler_or_payload

        response_body = {
            "id": "chatcmpl-mock-123",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            json.dumps(handler_or_payload)
                            if isinstance(handler_or_payload, dict)
                            else str(handler_or_payload)
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 340,
                "total_tokens": 460,
            },
        }
        resp_headers = headers or {"content-type": "application/json", "content-encoding": "identity"}
        payload_bytes = json.dumps(response_body).encode("utf-8")
        return httpx.Response(
            status_code=status_code,
            headers=resp_headers,
            stream=_CountingStream([payload_bytes]),
        )

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport, base_url="https://mock-provider.test/v1/")
    return client, recorded_requests


@pytest.fixture
def test_identities() -> dict[str, UUID]:
    return {
        "project_id": uuid4(),
        "chapter_id": uuid4(),
        "workflow_run_id": uuid4(),
        "outline_doc_id": uuid4(),
        "outline_ver_id": uuid4(),
        "source_doc_id": uuid4(),
        "source_ver_id": uuid4(),
        "seg1_id": uuid4(),
        "seg2_id": uuid4(),
    }


# ============================================================================
# Chapter Writer Provider Tests
# ============================================================================

@pytest.mark.anyio
async def test_writer_initial_draft_sends_structured_request_and_receives_candidate(
    test_identities: dict[str, UUID],
) -> None:
    allowed_segs = (
        AllowedChapterSegment(
            segment_id=test_identities["seg1_id"],
            index=1,
            title="The Grand Opening",
            brief="Detective arrives at misty dock.",
        ),
        AllowedChapterSegment(
            segment_id=test_identities["seg2_id"],
            index=2,
            title="Shadows in the Fog",
            brief="An unexpected contact slips away.",
        ),
    )
    request = InitialDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Chapter 1: The docks at midnight.",
        ),
        allowed_segments=allowed_segs,
        contexts=(
            WriterContextSnapshot(
                document_id=uuid4(),
                version_id=uuid4(),
                project_id=test_identities["project_id"],
                kind=WriterContextKind.STYLE_GUIDE,
                content="Atmospheric Victorian third-person limited prose.",
            ),
        ),
    )
    mock_payload = _make_sample_candidate_payload(request)
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        model="gpt-4o-mini-writer",
        timeout_seconds=45.0,
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        result = await agent.initial_draft(request)
        assert isinstance(result, CandidateChapterOutput)
        assert result.project_id == test_identities["project_id"]
        assert result.chapter_id == test_identities["chapter_id"]
        assert len(result.segments) == 2
        assert result.complete_chapter is True
        assert result.self_check.outline_followed is True

        # Verify transport invocation details
        assert len(recorded) == 1
        req_body = recorded[0]["json"]
        assert req_body["model"] == "gpt-4o-mini-writer"
        assert req_body["temperature"] == 0.0
        assert req_body["top_p"] == 1.0
        assert req_body["max_tokens"] == 16384
        assert req_body["response_format"]["type"] == "json_schema"
        assert req_body["response_format"]["json_schema"]["name"] == "candidate_chapter_output"

        # Verify messages
        system_msg = req_body["messages"][0]
        user_msg = req_body["messages"][1]
        assert system_msg["role"] == "system"
        assert "Point of View" in system_msg["content"]
        assert user_msg["role"] == "user"
        assert "The docks at midnight" in user_msg["content"]
        assert "The Grand Opening" in user_msg["content"]
        assert "Atmospheric Victorian" in user_msg["content"]

        # Verify provenance accounting
        assert provider.last_provenance is not None
        assert provider.last_provenance.provider_kind == "openai_compatible"
        assert provider.last_provenance.model_identifier == "gpt-4o-mini-writer"
        assert provider.last_input_tokens == 120
        assert provider.last_output_tokens == 340
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_writer_segment_draft_targets_subset_of_segments(
    test_identities: dict[str, UUID],
) -> None:
    allowed_segs = (
        AllowedChapterSegment(
            segment_id=test_identities["seg1_id"],
            index=1,
            title="Part 1",
            brief="First beat",
        ),
        AllowedChapterSegment(
            segment_id=test_identities["seg2_id"],
            index=2,
            title="Part 2",
            brief="Second beat",
        ),
    )
    request = SegmentDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat for segment drafting.",
        ),
        allowed_segments=allowed_segs,
        target_segment_ids=(test_identities["seg2_id"],),
    )
    mock_payload = _make_sample_candidate_payload(request)
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        result = await agent.segment_draft(request)
        assert len(result.segments) == 1
        assert result.segments[0].segment_id == test_identities["seg2_id"]
        assert result.complete_chapter is False

        user_content = recorded[0]["json"]["messages"][1]["content"]
        assert "[TARGET TO GENERATE/REVISE]" in user_content
        assert "[CONTEXT ONLY]" in user_content
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_revision_user_feedback_forwards_feedback_instructions(
    test_identities: dict[str, UUID],
) -> None:
    allowed_segs = (
        AllowedChapterSegment(
            segment_id=test_identities["seg1_id"],
            index=1,
            title="Opening",
            brief="Scene opening",
        ),
    )
    source_draft = SourceDraftReference(
        document_id=test_identities["source_doc_id"],
        version_id=test_identities["source_ver_id"],
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        segments=(
            SourceDraftSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Opening",
                content="Old opening prose before revision.",
            ),
        ),
    )
    request = UserFeedbackRevisionRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat for user revision.",
        ),
        allowed_segments=allowed_segs,
        target_segment_ids=(test_identities["seg1_id"],),
        source_draft=source_draft,
        feedback_refs=(
            UserFeedbackReference(
                feedback_id=uuid4(),
                project_id=test_identities["project_id"],
                chapter_id=test_identities["chapter_id"],
                workflow_run_id=test_identities["workflow_run_id"],
                source_draft_document_id=test_identities["source_doc_id"],
                source_draft_version_id=test_identities["source_ver_id"],
                instruction="Make the detective sound much more skeptical and weary.",
            ),
        ),
    )
    mock_payload = _make_sample_candidate_payload(request, text_prefix="Revised weary tone:")
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = RevisionAgent(provider)

    try:
        result = await agent.user_feedback_revision(request)
        assert result.source_draft_document_id == test_identities["source_doc_id"]
        assert result.source_draft_version_id == test_identities["source_ver_id"]
        assert "Revised weary tone" in result.segments[0].content

        user_content = recorded[0]["json"]["messages"][1]["content"]
        assert "Make the detective sound much more skeptical and weary." in user_content
        assert "Old opening prose before revision." in user_content
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_revision_review_driven_forwards_findings(
    test_identities: dict[str, UUID],
) -> None:
    allowed_segs = (
        AllowedChapterSegment(
            segment_id=test_identities["seg1_id"],
            index=1,
            title="Scene 1",
            brief="Scene 1 brief",
        ),
    )
    source_draft = SourceDraftReference(
        document_id=test_identities["source_doc_id"],
        version_id=test_identities["source_ver_id"],
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        segments=(
            SourceDraftSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Scene 1",
                content="Old draft text with pacing drag.",
            ),
        ),
    )
    request = ReviewDrivenRevisionRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat for review revision.",
        ),
        allowed_segments=allowed_segs,
        target_segment_ids=(test_identities["seg1_id"],),
        source_draft=source_draft,
        review_report_refs=(
            ReviewReportReference(
                report_id=test_identities["workflow_run_id"],
                project_id=test_identities["project_id"],
                chapter_id=test_identities["chapter_id"],
                workflow_run_id=test_identities["workflow_run_id"],
                target_draft_document_id=test_identities["source_doc_id"],
                target_draft_version_id=test_identities["source_ver_id"],
                summary="Editor found pacing drag in the middle section.",
            ),
        ),
        selected_findings=(
            SelectedReviewFinding(
                report_id=test_identities["workflow_run_id"],
                finding=ChapterReviewFinding(
                    sequence=1,
                    code="pacing_drag_scene_1",
                    severity=ReviewFindingSeverity.WARNING,
                    required=False,
                    evidence_segment_ids=(test_identities["seg1_id"],),
                    rationale="Unnecessary dialogue padding slows narrative momentum.",
                    suggested_action="Condense the exchange and introduce the ticking clock.",
                ),
            ),
        ),
    )
    mock_payload = _make_sample_candidate_payload(request, text_prefix="Condensed brisk scene:")
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = RevisionAgent(provider)

    try:
        result = await agent.review_driven_revision(request)
        assert len(result.segments) == 1
        user_content = recorded[0]["json"]["messages"][1]["content"]
        assert "pacing_drag_scene_1" in user_content
        assert "Condense the exchange and introduce the ticking clock." in user_content
    finally:
        await provider.aclose()


# ============================================================================
# Chapter Review Provider Tests
# ============================================================================

@pytest.mark.anyio
async def test_editor_review_produces_valid_report(
    test_identities: dict[str, UUID],
) -> None:
    target = ChapterReviewTarget(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        document_id=test_identities["source_doc_id"],
        version_id=test_identities["source_ver_id"],
        segments=(
            ReviewSegmentSnapshot(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Chapter 1",
                content="The heavy iron knocker echoed through the empty hall.",
            ),
        ),
    )
    request = EditorReviewRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        target=target,
        approved_outline=ApprovedOutlineSnapshot(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat for editorial review.",
        ),
        contexts=(
            ReviewContextSnapshot(
                document_id=uuid4(),
                version_id=uuid4(),
                project_id=test_identities["project_id"],
                kind=ReviewContextKind.STYLE_GUIDE,
                content="Sensory precision and period immersion.",
            ),
        ),
    )
    mock_payload = _make_sample_review_payload(
        request,
        passed=True,
        reviewer_role="editor_agent",
        review_mode="chapter_editor",
    )
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterReviewProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        model="editor-model-v1",
        client=client,
    )
    agent = EditorAgent(provider)

    try:
        report = await agent.review(request)
        assert isinstance(report, ChapterReviewReport)
        assert report.passed is True
        assert report.reviewer_role == ReviewerRole.EDITOR
        assert report.review_mode == "chapter_editor"
        assert report.target_document_id == test_identities["source_doc_id"]
        assert report.target_version_id == test_identities["source_ver_id"]

        req_body = recorded[0]["json"]
        assert req_body["model"] == "editor-model-v1"
        assert req_body["response_format"]["json_schema"]["name"] == "chapter_review_report"
        user_content = req_body["messages"][1]["content"]
        assert "The heavy iron knocker echoed" in user_content
        assert "Sensory precision and period immersion" in user_content
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_chief_editor_review_detects_blocking_finding(
    test_identities: dict[str, UUID],
) -> None:
    target = ChapterReviewTarget(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        document_id=test_identities["source_doc_id"],
        version_id=test_identities["source_ver_id"],
        segments=(
            ReviewSegmentSnapshot(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Climax",
                content="Detective suddenly gave up without any resistance.",
            ),
        ),
    )
    request = ChiefEditorChapterFinalRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        target=target,
        approved_outline=ApprovedOutlineSnapshot(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat requiring high stakes confrontation.",
        ),
        contexts=(
            ReviewContextSnapshot(
                document_id=uuid4(),
                version_id=uuid4(),
                project_id=test_identities["project_id"],
                kind=ReviewContextKind.AUDIENCE_GOAL,
                content="Maintain tension and climax.",
            ),
        ),
    )
    mock_payload = _make_sample_review_payload(
        request,
        passed=False,
        reviewer_role="chief_editor_agent",
        review_mode="chapter_chief_final",
    )
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterReviewProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = ChiefEditorChapterFinalAgent(provider)

    try:
        report = await agent.review(request)
        assert report.passed is False
        assert report.reviewer_role == ReviewerRole.CHIEF_EDITOR
        assert len(report.findings) == 1
        assert report.findings[0].severity == ReviewFindingSeverity.BLOCKING
        assert report.findings[0].required is True
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_lore_review_passes_clean_chapter(
    test_identities: dict[str, UUID],
) -> None:
    target = ChapterReviewTarget(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        document_id=test_identities["source_doc_id"],
        version_id=test_identities["source_ver_id"],
        segments=(
            ReviewSegmentSnapshot(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Observatory",
                content="The brass celestial armillary turned on steam-pressured gears.",
            ),
        ),
    )
    request = LoreChapterFinalRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        target=target,
        approved_outline=ApprovedOutlineSnapshot(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Outline beat in the brass celestial observatory.",
        ),
        contexts=(
            ReviewContextSnapshot(
                document_id=uuid4(),
                version_id=uuid4(),
                project_id=test_identities["project_id"],
                kind=ReviewContextKind.LORE_BOUNDARY,
                content="Brass celestial clockwork physics.",
            ),
        ),
    )
    mock_payload = _make_sample_review_payload(
        request,
        passed=True,
        reviewer_role="lore_agent",
        review_mode="chapter_final_lore",
    )
    client, recorded = _make_mock_client(mock_payload)

    provider = OpenAICompatibleChapterReviewProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = LoreChapterFinalAgent(provider)

    try:
        report = await agent.review(request)
        assert report.passed is True
        assert report.reviewer_role == ReviewerRole.LORE
    finally:
        await provider.aclose()


# ============================================================================
# Failure Mode & Invariant Tests
# ============================================================================

@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (429, ProviderRateLimitedError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
async def test_provider_maps_http_errors_safely(
    test_identities: dict[str, UUID],
    status_code: int,
    expected_error: type[Exception],
) -> None:
    request = InitialDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Sample outline beat.",
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Scene 1",
                brief="Scene 1 brief",
            ),
        ),
    )
    client, _ = _make_mock_client({"error": "upstream failure"}, status_code=status_code)
    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        with pytest.raises(expected_error):
            await agent.initial_draft(request)
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_provider_maps_timeout_error(
    test_identities: dict[str, UUID],
) -> None:
    request = InitialDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Sample outline beat.",
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Scene 1",
                brief="Scene 1 brief",
            ),
        ),
    )
    client, _ = _make_mock_client(httpx.ReadTimeout("Connection timed out"))
    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        with pytest.raises(ProviderTimeoutError):
            await agent.initial_draft(request)
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_provider_rejects_malformed_json_output(
    test_identities: dict[str, UUID],
) -> None:
    request = InitialDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Sample outline beat.",
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Scene 1",
                brief="Scene 1 brief",
            ),
        ),
    )
    client, _ = _make_mock_client("not-valid-json")
    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        with pytest.raises(ProviderInvalidOutputError):
            await agent.initial_draft(request)
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_provider_rejects_lineage_tampering(
    test_identities: dict[str, UUID],
) -> None:
    request = InitialDraftRequest(
        project_id=test_identities["project_id"],
        chapter_id=test_identities["chapter_id"],
        workflow_run_id=test_identities["workflow_run_id"],
        approved_outline=ApprovedOutlineReference(
            document_id=test_identities["outline_doc_id"],
            version_id=test_identities["outline_ver_id"],
            project_id=test_identities["project_id"],
            chapter_id=test_identities["chapter_id"],
            content="Sample outline beat.",
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=test_identities["seg1_id"],
                index=1,
                title="Scene 1",
                brief="Scene 1 brief",
            ),
        ),
    )
    # Output has a forged chapter_id
    payload = _make_sample_candidate_payload(request)
    payload["chapter_id"] = str(uuid4())

    client, _ = _make_mock_client(payload)
    provider = OpenAICompatibleChapterWriterProvider(
        base_url="https://mock-provider.test/v1",
        api_key="secret-token",
        client=client,
    )
    agent = WriterAgent(provider)

    try:
        with pytest.raises(ProviderInvalidOutputError):
            await agent.initial_draft(request)
    finally:
        await provider.aclose()


# ============================================================================
# Dependency Injection & Configuration Tests
# ============================================================================

def test_v2_composition_defaults_to_deterministic_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps_module, "settings", make_settings(chapter_production_provider="fake"))
    composition = get_chapter_production_v2_composition()

    assert isinstance(composition.writer_agent._provider, DeterministicChapterWriterProvider)
    assert isinstance(composition.editor_agent._provider, DeterministicChapterReviewProvider)


def test_v2_composition_fails_closed_when_openai_compatible_configured_incompletely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deps_module,
        "settings",
        make_settings(
            chapter_production_provider="openai_compatible",
            openai_compatible_base_url="https://api.openai.com/v1",
            openai_compatible_api_key=None,  # Missing key
            openai_compatible_model="gpt-4o",
        ),
    )
    with pytest.raises(ProviderConfigurationError):
        get_chapter_production_v2_composition()


def test_v2_composition_instantiates_real_providers_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deps_module,
        "settings",
        make_settings(
            chapter_production_provider="openai_compatible",
            openai_compatible_base_url="https://api.openai.com/v1",
            openai_compatible_api_key=SecretStr("real-secret-token"),
            openai_compatible_model="gpt-4o-mini",
            openai_compatible_timeout_seconds=40.0,
        ),
    )
    composition = get_chapter_production_v2_composition()

    assert isinstance(composition.writer_agent._provider, OpenAICompatibleChapterWriterProvider)
    assert isinstance(composition.editor_agent._provider, OpenAICompatibleChapterReviewProvider)
    assert isinstance(composition.chief_editor_agent._provider, OpenAICompatibleChapterReviewProvider)
    assert isinstance(composition.lore_agent._provider, OpenAICompatibleChapterReviewProvider)
    assert not isinstance(composition.writer_agent._provider, DeterministicChapterWriterProvider)
