from copy import deepcopy
from pathlib import Path
import socket
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ConfigDict, ValidationError

from app.agents import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    CandidateChapterOutput,
    DeterministicChapterWriterProvider,
    InitialDraftRequest,
    ProfileRegistry,
    ProfileRegistryError,
    RevisionAgent,
    ReviewDrivenRevisionRequest,
    ReviewReportReference,
    SegmentDraftRequest,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
    WriterAgent,
    canonical_chapter_json_bytes,
)
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
OUTLINE_DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
OUTLINE_VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
DRAFT_DOCUMENT_ID = UUID("66666666-6666-4666-8666-666666666666")
DRAFT_VERSION_ID = UUID("77777777-7777-4777-8777-777777777777")
SEGMENT_ONE_ID = UUID("88888888-8888-4888-8888-888888888888")
SEGMENT_TWO_ID = UUID("99999999-9999-4999-8999-999999999999")
FEEDBACK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REPORT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def outline_ref(**updates: object) -> ApprovedOutlineReference:
    values = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": OUTLINE_DOCUMENT_ID,
        "version_id": OUTLINE_VERSION_ID,
    }
    values.update(updates)
    return ApprovedOutlineReference(**values)


def segments() -> list[AllowedChapterSegment]:
    return [
        AllowedChapterSegment(
            segment_id=SEGMENT_ONE_ID,
            index=1,
            title="Arrival",
            brief="The protagonist reaches the sealed city.",
        ),
        AllowedChapterSegment(
            segment_id=SEGMENT_TWO_ID,
            index=2,
            title="Warning",
            brief="A guide warns that the city remembers names.",
        ),
    ]


def source_segments() -> list[SourceDraftSegment]:
    return [
        SourceDraftSegment(
            segment_id=SEGMENT_ONE_ID,
            index=1,
            title="Arrival",
            content="The protagonist crosses the gate and hides their true reason.",
        ),
        SourceDraftSegment(
            segment_id=SEGMENT_TWO_ID,
            index=2,
            title="Warning",
            content="The guide warns the protagonist, but gives no concrete consequence.",
        ),
    ]


def source_ref(**updates: object) -> SourceDraftReference:
    values = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": DRAFT_DOCUMENT_ID,
        "version_id": DRAFT_VERSION_ID,
        "segments": source_segments(),
    }
    values.update(updates)
    return SourceDraftReference(**values)


def initial_request() -> InitialDraftRequest:
    return InitialDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=outline_ref(),
        allowed_segments=segments(),
    )


def segment_request() -> SegmentDraftRequest:
    return SegmentDraftRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=outline_ref(),
        allowed_segments=segments(),
        target_segment_ids=[SEGMENT_TWO_ID],
    )


def feedback_request() -> UserFeedbackRevisionRequest:
    return UserFeedbackRevisionRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=outline_ref(),
        source_draft=source_ref(),
        allowed_segments=segments(),
        target_segment_ids=[SEGMENT_ONE_ID],
        feedback_refs=[
            UserFeedbackReference(
                feedback_id=FEEDBACK_ID,
                project_id=PROJECT_ID,
                chapter_id=CHAPTER_ID,
                workflow_run_id=RUN_ID,
                source_draft_document_id=DRAFT_DOCUMENT_ID,
                source_draft_version_id=DRAFT_VERSION_ID,
                instruction="Clarify why the protagonist enters the city.",
            )
        ],
    )


def review_request() -> ReviewDrivenRevisionRequest:
    return ReviewDrivenRevisionRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=outline_ref(),
        source_draft=source_ref(),
        allowed_segments=segments(),
        target_segment_ids=[SEGMENT_TWO_ID],
        review_report_refs=[
            ReviewReportReference(
                report_id=REPORT_ID,
                project_id=PROJECT_ID,
                chapter_id=CHAPTER_ID,
                workflow_run_id=RUN_ID,
                target_draft_document_id=DRAFT_DOCUMENT_ID,
                target_draft_version_id=DRAFT_VERSION_ID,
                summary="The warning lacks a concrete consequence.",
            )
        ],
    )


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("writer_agent", "initial_draft"),
        ("writer_agent", "segment_draft"),
        ("revision_agent", "user_feedback_revision"),
        ("revision_agent", "review_driven_revision"),
    ],
)
def test_writer_profiles_are_exact_allowlisted_non_authoritative(name: str, mode: str) -> None:
    profile = ProfileRegistry().load(name, mode)
    assert profile.output_schema == "candidate_chapter_output"
    assert set(profile.permissions.can_write) == {"candidate_chapter"}
    assert set(profile.permissions.cannot) == {
        "network",
        "credentials",
        "filesystem",
        "database",
        "document_service",
        "document_versions",
    }


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("writer_agent", None),
        ("writer_agent", "review_driven_revision"),
        ("revision_agent", "initial_draft"),
        ("revision_agent", "unknown"),
    ],
)
def test_writer_profiles_fail_closed_outside_allowlist(name: str, mode: str | None) -> None:
    with pytest.raises(ProfileRegistryError):
        ProfileRegistry().load(name, mode)


def test_writer_profile_manifest_rejects_permission_tampering(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "app" / "agents" / "profiles" / "writer_initial_draft.yaml"
    text = source.read_text().replace(
        "    - allowed_segments\n  can_write:",
        "    - allowed_segments\n    - arbitrary_files\n  can_write:",
    )
    (tmp_path / source.name).write_text(text)
    with pytest.raises(ProfileRegistryError):
        ProfileRegistry(tmp_path).load("writer_agent", "initial_draft")


@pytest.mark.parametrize(
    "factory", [initial_request, segment_request, feedback_request, review_request]
)
def test_requests_are_strict_immutable_and_expose_no_authority(factory: object) -> None:
    request = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError):
        type(request).model_validate({**request.model_dump(), "workspace_path": "private.md"})
    with pytest.raises(ValidationError):
        request.project_id = uuid4()
    forbidden = {
        "path",
        "api_key",
        "credentials",
        "provider",
        "session",
        "document_service",
        "canonical",
    }
    assert forbidden.isdisjoint(type(request).model_fields)


def test_requests_reject_cross_project_stale_duplicate_unknown_and_contradictory_refs() -> None:
    with pytest.raises(ValidationError):
        InitialDraftRequest(
            **{
                **initial_request().model_dump(),
                "approved_outline": outline_ref(project_id=uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        UserFeedbackRevisionRequest(
            **{
                **feedback_request().model_dump(),
                "feedback_refs": [
                    feedback_request().feedback_refs[0],
                    feedback_request().feedback_refs[0],
                ],
            }
        )
    stale = (
        feedback_request().feedback_refs[0].model_copy(update={"source_draft_version_id": uuid4()})
    )
    with pytest.raises(ValidationError):
        UserFeedbackRevisionRequest(**{**feedback_request().model_dump(), "feedback_refs": [stale]})
    with pytest.raises(ValidationError):
        SegmentDraftRequest(**{**segment_request().model_dump(), "target_segment_ids": [uuid4()]})
    with pytest.raises(ValidationError):
        SegmentDraftRequest(
            **{
                **segment_request().model_dump(),
                "target_segment_ids": [SEGMENT_TWO_ID, SEGMENT_ONE_ID],
            }
        )
    contradictory = segments()
    contradictory[1] = contradictory[1].model_copy(update={"index": 1})
    with pytest.raises(ValidationError):
        InitialDraftRequest(**{**initial_request().model_dump(), "allowed_segments": contradictory})


def test_requests_reject_oversized_transient_material_without_echoing_it() -> None:
    huge = "secret novel material " * 20_000
    payload = feedback_request().model_dump()
    payload["feedback_refs"][0]["instruction"] = huge
    with pytest.raises(ValidationError) as error:
        UserFeedbackRevisionRequest.model_validate(payload)
    assert huge[:100] not in str(error.value)


def test_requests_reject_source_snapshots_that_do_not_match_allowed_segments() -> None:
    mismatched = source_segments()
    mismatched[0] = mismatched[0].model_copy(update={"segment_id": uuid4()})
    with pytest.raises(ValidationError):
        UserFeedbackRevisionRequest(
            **{
                **feedback_request().model_dump(),
                "source_draft": source_ref(segments=mismatched),
            }
        )


def test_requests_reject_aggregate_utf8_envelopes_over_the_context_budget() -> None:
    huge_segments = [
        AllowedChapterSegment(
            segment_id=uuid4(),
            index=index,
            title=f"Segment {index}",
            brief="界" * 8000,
        )
        for index in range(1, 65)
    ]
    with pytest.raises(ValidationError):
        InitialDraftRequest(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            workflow_run_id=RUN_ID,
            approved_outline=outline_ref(),
            allowed_segments=huge_segments,
        )


@pytest.mark.anyio
async def test_writer_and_revision_fakes_are_byte_stable_and_bound_to_requests() -> None:
    provider = DeterministicChapterWriterProvider()
    calls = (
        (WriterAgent(provider).initial_draft, initial_request()),
        (WriterAgent(provider).segment_draft, segment_request()),
        (RevisionAgent(provider).user_feedback_revision, feedback_request()),
        (RevisionAgent(provider).review_driven_revision, review_request()),
    )
    for call, request in calls:
        first = await call(request)
        second = await call(request)
        assert canonical_chapter_json_bytes(first) == canonical_chapter_json_bytes(second)
        assert first.project_id == PROJECT_ID
        assert first.chapter_id == CHAPTER_ID
        assert first.workflow_run_id == RUN_ID
        assert first.approved_outline_document_id == OUTLINE_DOCUMENT_ID
        assert first.approved_outline_version_id == OUTLINE_VERSION_ID
        expected_source_document = (
            None
            if isinstance(request, (InitialDraftRequest, SegmentDraftRequest))
            else DRAFT_DOCUMENT_ID
        )
        assert first.source_draft_document_id == expected_source_document
        assert tuple(item.segment_id for item in first.segments) == request.target_segment_ids
        assert "canonical" not in CandidateChapterOutput.model_fields

    changed_source = source_ref(
        segments=[
            source_segments()[0].model_copy(update={"content": "A different bound source."}),
            source_segments()[1],
        ]
    )
    changed_request = UserFeedbackRevisionRequest(
        **{**feedback_request().model_dump(), "source_draft": changed_source}
    )
    baseline = await RevisionAgent(DeterministicChapterWriterProvider()).user_feedback_revision(
        feedback_request()
    )
    changed = await RevisionAgent(DeterministicChapterWriterProvider()).user_feedback_revision(
        changed_request
    )
    assert canonical_chapter_json_bytes(baseline) != canonical_chapter_json_bytes(changed)


@pytest.mark.anyio
async def test_output_validation_rejects_redirects_unknown_segments_stale_versions_and_extras() -> (
    None
):
    request = feedback_request()
    raw = await DeterministicChapterWriterProvider().revise_from_user_feedback(
        request, ProfileRegistry().load("revision_agent", "user_feedback_revision")
    )
    assert isinstance(raw, dict)
    invalid_payloads = []
    for field in (
        "project_id",
        "chapter_id",
        "workflow_run_id",
        "approved_outline_document_id",
        "approved_outline_version_id",
    ):
        payload = deepcopy(raw)
        payload[field] = str(uuid4())
        invalid_payloads.append(payload)
    stale = deepcopy(raw)
    stale["source_draft_version_id"] = str(uuid4())
    invalid_payloads.append(stale)
    stale_document = deepcopy(raw)
    stale_document["source_draft_document_id"] = str(uuid4())
    invalid_payloads.append(stale_document)
    unknown = deepcopy(raw)
    unknown["segments"][0]["segment_id"] = str(uuid4())
    invalid_payloads.append(unknown)
    extra = deepcopy(raw)
    extra["segments"][0]["path"] = "chapters/private.md"
    invalid_payloads.append(extra)
    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError) as error:
            RevisionAgent.validate_output(payload, request=request)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


def test_output_rejects_duplicate_or_oversized_segments_and_contradictory_completion() -> None:
    request = initial_request()
    provider = DeterministicChapterWriterProvider()
    raw = provider.initial_draft_sync(request)
    duplicate = deepcopy(raw)
    duplicate["segments"].append(deepcopy(duplicate["segments"][0]))
    oversized = deepcopy(raw)
    oversized["segments"][0]["content"] = "x" * 262_145
    oversized_utf8 = deepcopy(raw)
    oversized_utf8["segments"][0]["content"] = "界" * 100_000
    aggregate_oversized = deepcopy(raw)
    aggregate_oversized["segments"] = [
        {
            **deepcopy(raw["segments"][0]),
            "segment_id": str(uuid4()),
            "index": index,
            "content": "x" * 180_000,
        }
        for index in range(1, 4)
    ]
    contradictory = deepcopy(raw)
    contradictory["complete_chapter"] = False
    reversed_segments = deepcopy(raw)
    reversed_segments["segments"].reverse()
    for payload in (
        duplicate,
        oversized,
        oversized_utf8,
        aggregate_oversized,
        contradictory,
        reversed_segments,
    ):
        with pytest.raises(ProviderInvalidOutputError):
            WriterAgent.validate_output(payload, request=request)


@pytest.mark.anyio
async def test_agent_boundary_revalidates_constructed_request() -> None:
    bypassed = InitialDraftRequest.model_construct(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        approved_outline=outline_ref(project_id=uuid4()),
        allowed_segments=tuple(segments()),
    )
    with pytest.raises(ProviderConfigurationError) as error:
        await WriterAgent(DeterministicChapterWriterProvider()).initial_draft(bypassed)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.anyio
async def test_agent_boundary_rejects_a_valid_request_for_the_wrong_mode() -> None:
    with pytest.raises(ProviderConfigurationError):
        await WriterAgent(DeterministicChapterWriterProvider()).initial_draft(
            segment_request()  # type: ignore[arg-type]
        )


def test_output_boundary_rejects_foreign_models_that_can_hide_unknown_fields() -> None:
    class PermissiveProviderOutput(CandidateChapterOutput):
        model_config = ConfigDict(extra="ignore")

    raw = DeterministicChapterWriterProvider().initial_draft_sync(initial_request())
    raw["hidden_authority"] = "database"
    with pytest.raises(ProviderInvalidOutputError):
        WriterAgent.validate_output(
            PermissiveProviderOutput.model_validate(raw),
            request=initial_request(),
        )


class _WrongProvider:
    async def draft_initial(self, request: object, profile: object) -> object:
        return {}


class _ExplodingProvider:
    async def draft_initial(self, request: object, profile: object) -> object:
        raise RuntimeError("https://provider.invalid sk-secret full novel body")


class _SafeProvider:
    async def draft_initial(self, request: object, profile: object) -> object:
        try:
            raise RuntimeError("leaky provider message")
        except RuntimeError as cause:
            raise ProviderTimeoutError() from cause


@pytest.mark.anyio
async def test_agent_capability_and_provider_failures_are_safely_normalized() -> None:
    with pytest.raises(ProviderConfigurationError) as wrong:
        await RevisionAgent(_WrongProvider()).user_feedback_revision(feedback_request())
    with pytest.raises(ProviderUnavailableError) as unexpected:
        await WriterAgent(_ExplodingProvider()).initial_draft(initial_request())
    with pytest.raises(ProviderTimeoutError) as safe:
        await WriterAgent(_SafeProvider()).initial_draft(initial_request())
    for error in (wrong, unexpected, safe):
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        rendered = repr(error.value) + str(error.value)
        assert "provider.invalid" not in rendered
        assert "sk-secret" not in rendered
        assert "leaky provider message" not in rendered


@pytest.mark.anyio
async def test_deterministic_fake_uses_no_network_filesystem_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("external authority was used")

    async def blocked_async(*args: object, **kwargs: object) -> object:
        raise AssertionError("external authority was used")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked_async)
    profile = ProfileRegistry().load("writer_agent", "initial_draft")
    monkeypatch.setattr(Path, "read_text", blocked)
    monkeypatch.setattr(Path, "write_text", blocked)
    provider = DeterministicChapterWriterProvider()
    raw = await provider.draft_initial(initial_request(), profile)
    assert raw["segments"]
