from copy import deepcopy
import os
from pathlib import Path
import random
import socket
import time
from uuid import UUID, uuid4
import warnings

import httpx
import pytest
from pydantic import ConfigDict, ValidationError

from app.agents import (
    ApprovedOutlineSnapshot,
    ChapterReviewReport,
    ChapterReviewTarget,
    ChiefEditorChapterFinalAgent,
    ChiefEditorChapterFinalRequest,
    DeterministicChapterReviewProvider,
    EditorAgent,
    EditorReviewRequest,
    LoreChapterFinalAgent,
    LoreChapterFinalRequest,
    ProfileRegistry,
    ProfileRegistryError,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewFindingSeverity,
    ReviewerRole,
    ReviewSegmentSnapshot,
    canonical_review_json_bytes,
    validate_chapter_review_report,
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
TARGET_DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
TARGET_VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
OUTLINE_DOCUMENT_ID = UUID("66666666-6666-4666-8666-666666666666")
OUTLINE_VERSION_ID = UUID("77777777-7777-4777-8777-777777777777")
SEGMENT_ONE_ID = UUID("88888888-8888-4888-8888-888888888888")
SEGMENT_TWO_ID = UUID("99999999-9999-4999-8999-999999999999")


def target(**updates: object) -> ChapterReviewTarget:
    values = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": TARGET_DOCUMENT_ID,
        "version_id": TARGET_VERSION_ID,
        "segments": [
            ReviewSegmentSnapshot(
                segment_id=SEGMENT_ONE_ID,
                index=1,
                title="Arrival",
                content="The protagonist enters the sealed city.",
            ),
            ReviewSegmentSnapshot(
                segment_id=SEGMENT_TWO_ID,
                index=2,
                title="Warning",
                content="A guide gives a warning before the gate closes.",
            ),
        ],
    }
    values.update(updates)
    return ChapterReviewTarget(**values)


def outline(**updates: object) -> ApprovedOutlineSnapshot:
    values = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": OUTLINE_DOCUMENT_ID,
        "version_id": OUTLINE_VERSION_ID,
        "content": "Arrival establishes the goal; warning ends on a hook.",
    }
    values.update(updates)
    return ApprovedOutlineSnapshot(**values)


def context(kind: ReviewContextKind) -> ReviewContextSnapshot:
    number = list(ReviewContextKind).index(kind) + 1
    return ReviewContextSnapshot(
        project_id=PROJECT_ID,
        document_id=UUID(f"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa{number}"),
        version_id=UUID(f"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb{number}"),
        kind=kind,
        content=f"Bound context for {kind.value}.",
    )


def editor_request() -> EditorReviewRequest:
    return EditorReviewRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=target(),
        approved_outline=outline(),
        contexts=[context(ReviewContextKind.STYLE_GUIDE)],
    )


def chief_request() -> ChiefEditorChapterFinalRequest:
    return ChiefEditorChapterFinalRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=target(),
        approved_outline=outline(),
        contexts=[context(ReviewContextKind.AUDIENCE_GOAL)],
    )


def lore_request() -> LoreChapterFinalRequest:
    return LoreChapterFinalRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=target(),
        approved_outline=outline(),
        contexts=[
            context(ReviewContextKind.LORE_BOUNDARY),
            context(ReviewContextKind.CHARACTER_STATE),
            context(ReviewContextKind.TIMELINE),
        ],
    )


@pytest.mark.parametrize(
    ("name", "mode", "required_reads"),
    [
        (
            "editor_agent",
            None,
            {"target_chapter", "approved_outline", "editor_context"},
        ),
        (
            "chief_editor",
            "chapter_final",
            {"target_chapter", "approved_outline", "chief_editor_context"},
        ),
        (
            "lore_agent",
            "chapter_final",
            {"target_chapter", "approved_outline", "lore_context"},
        ),
    ],
)
def test_review_profiles_are_exact_allowlisted_and_advisory(
    name: str, mode: str | None, required_reads: set[str]
) -> None:
    profile = ProfileRegistry().load(name, mode)
    assert profile.output_schema == "chapter_review_report"
    assert set(profile.permissions.can_read) == required_reads
    assert profile.permissions.can_write == []
    assert set(profile.permissions.cannot) == {
        "network",
        "credentials",
        "filesystem",
        "database",
        "orm",
        "document_service",
        "document_versions",
        "revise_chapter",
        "approve_actions",
        "resolve_actions",
        "workflow_transitions",
    }


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("editor_agent", "chapter_editor"),
        ("chief_editor", "chapter_chief_final"),
        ("lore_agent", None),
        ("lore_agent", "unknown"),
    ],
)
def test_review_profiles_fail_closed_outside_exact_allowlist(name: str, mode: str | None) -> None:
    with pytest.raises(ProfileRegistryError):
        ProfileRegistry().load(name, mode)


def test_review_profile_manifest_rejects_authority_tampering(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "app" / "agents" / "profiles" / "editor_review.yaml"
    text = source.read_text().replace("  can_write: []", "  can_write:\n    - chapter_draft")
    (tmp_path / source.name).write_text(text)
    with pytest.raises(ProfileRegistryError):
        ProfileRegistry(tmp_path).load("editor_agent")


@pytest.mark.parametrize("factory", [editor_request, chief_request, lore_request])
def test_requests_are_strict_immutable_bound_and_expose_no_authority(factory: object) -> None:
    request = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError):
        type(request).model_validate({**request.model_dump(), "workspace_path": "secret.md"})
    with pytest.raises(ValidationError):
        request.project_id = uuid4()
    assert {
        "path",
        "api_key",
        "credentials",
        "provider",
        "session",
        "document_service",
        "workflow_transition",
    }.isdisjoint(type(request).model_fields)


def test_requests_reject_cross_project_refs_duplicate_segments_and_wrong_context() -> None:
    with pytest.raises(ValidationError):
        EditorReviewRequest(
            **{**editor_request().model_dump(), "target": target(project_id=uuid4())}
        )
    duplicated = list(target().segments) + [target().segments[0]]
    with pytest.raises(ValidationError):
        EditorReviewRequest(
            **{**editor_request().model_dump(), "target": target(segments=duplicated)}
        )
    with pytest.raises(ValidationError):
        EditorReviewRequest(
            **{
                **editor_request().model_dump(),
                "contexts": [context(ReviewContextKind.LORE_BOUNDARY)],
            }
        )
    with pytest.raises(ValidationError):
        LoreChapterFinalRequest(
            **{
                **lore_request().model_dump(),
                "contexts": [context(ReviewContextKind.STYLE_GUIDE)],
            }
        )
    same_document_new_version = context(ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY).model_copy(
        update={
            "document_id": context(ReviewContextKind.STYLE_GUIDE).document_id,
            "version_id": uuid4(),
        }
    )
    with pytest.raises(ValidationError):
        EditorReviewRequest(
            **{
                **editor_request().model_dump(),
                "contexts": [
                    context(ReviewContextKind.STYLE_GUIDE),
                    same_document_new_version,
                ],
            }
        )


@pytest.mark.parametrize("factory", [editor_request, chief_request, lore_request])
def test_requests_require_mode_specific_context(factory: object) -> None:
    request = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError):
        type(request).model_validate({**request.model_dump(), "contexts": []})


def test_requests_reject_oversized_utf8_envelope_without_echoing_content() -> None:
    secret = "机密正文" * 100_000
    payload = editor_request().model_dump()
    payload["target"]["segments"][0]["content"] = secret
    with pytest.raises(ValidationError) as error:
        EditorReviewRequest.model_validate(payload)
    assert secret[:100] not in str(error.value)


@pytest.mark.parametrize("control", ["\x07", "\x1b"])
def test_requests_reject_unsafe_control_characters(control: str) -> None:
    payload = editor_request().model_dump()
    payload["contexts"][0]["content"] = f"safe{control}unsafe"
    with pytest.raises(ValidationError):
        EditorReviewRequest.model_validate(payload)


@pytest.mark.parametrize("outcome", ["passed", "warning", "blocking"])
@pytest.mark.anyio
async def test_deterministic_fake_outcomes_are_byte_stable_and_exactly_bound(
    outcome: str,
) -> None:
    provider = DeterministicChapterReviewProvider(outcome=outcome)
    calls = (
        (
            EditorAgent(provider).review,
            editor_request(),
            "editor_agent",
            "chapter_editor",
        ),
        (
            ChiefEditorChapterFinalAgent(provider).review,
            chief_request(),
            "chief_editor_agent",
            "chapter_chief_final",
        ),
        (
            LoreChapterFinalAgent(provider).review,
            lore_request(),
            "lore_agent",
            "chapter_final_lore",
        ),
    )
    for call, request, role, mode in calls:
        first = await call(request)
        second = await call(request)
        assert canonical_review_json_bytes(first) == canonical_review_json_bytes(second)
        assert first.reviewer_role.value == role
        assert first.review_mode == mode
        assert first.project_id == PROJECT_ID
        assert first.chapter_id == CHAPTER_ID
        assert first.workflow_run_id == RUN_ID
        assert first.target_document_id == TARGET_DOCUMENT_ID
        assert first.target_version_id == TARGET_VERSION_ID
        blocking = [
            finding
            for finding in first.findings
            if finding.severity is ReviewFindingSeverity.BLOCKING
        ]
        assert first.passed is (outcome != "blocking")
        assert bool(blocking) is (outcome == "blocking")
        assert {
            "report_id",
            "revised_text",
            "approval",
            "resolved",
            "workflow_transition",
        }.isdisjoint(ChapterReviewReport.model_fields)


@pytest.mark.anyio
async def test_deterministic_fake_consumes_context_without_echoing_it() -> None:
    first_request = editor_request()
    payload = first_request.model_dump()
    secret = "DIFFERENT_PRIVATE_CONTEXT"
    payload["contexts"][0]["content"] = secret
    second_request = EditorReviewRequest.model_validate(payload)
    provider = DeterministicChapterReviewProvider(outcome="warning")

    first = await EditorAgent(provider).review(first_request)
    second = await EditorAgent(provider).review(second_request)

    assert canonical_review_json_bytes(first) != canonical_review_json_bytes(second)
    assert secret.encode() not in canonical_review_json_bytes(second)


@pytest.mark.anyio
async def test_output_validation_rejects_redirect_unknown_evidence_and_extras() -> None:
    request = editor_request()
    raw = await DeterministicChapterReviewProvider(outcome="warning").review_editor(
        request, ProfileRegistry().load("editor_agent")
    )
    assert isinstance(raw, dict)
    invalid_payloads: list[dict[str, object]] = []
    for field in (
        "project_id",
        "chapter_id",
        "workflow_run_id",
        "target_document_id",
        "target_version_id",
    ):
        payload = deepcopy(raw)
        payload[field] = str(uuid4())
        invalid_payloads.append(payload)
    wrong_role = deepcopy(raw)
    wrong_role["reviewer_role"] = "lore_agent"
    invalid_payloads.append(wrong_role)
    wrong_mode = deepcopy(raw)
    wrong_mode["review_mode"] = "chapter_final_lore"
    invalid_payloads.append(wrong_mode)
    unknown_evidence = deepcopy(raw)
    unknown_evidence["findings"][0]["evidence_segment_ids"] = [str(uuid4())]
    invalid_payloads.append(unknown_evidence)
    noncanonical_evidence = deepcopy(raw)
    noncanonical_evidence["findings"][0]["evidence_segment_ids"] = [
        str(SEGMENT_TWO_ID),
        str(SEGMENT_ONE_ID),
    ]
    invalid_payloads.append(noncanonical_evidence)
    extra = deepcopy(raw)
    extra["findings"][0]["replacement_text"] = "provider rewrite"
    invalid_payloads.append(extra)
    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError) as error:
            EditorAgent.validate_output(payload, request=request)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


def test_output_rejects_contradictions_duplicates_unknown_severity_and_oversize() -> None:
    request = editor_request()
    raw = DeterministicChapterReviewProvider(outcome="blocking").editor_sync(request)
    passed_blocking = deepcopy(raw)
    passed_blocking["passed"] = True
    failed_without_blocking = deepcopy(raw)
    failed_without_blocking["findings"][0]["severity"] = "warning"
    failed_without_blocking["findings"][0]["required"] = False
    severity_contradiction = deepcopy(raw)
    severity_contradiction["findings"][0]["required"] = False
    duplicate = deepcopy(raw)
    duplicate["findings"].append(deepcopy(duplicate["findings"][0]))
    unknown_severity = deepcopy(raw)
    unknown_severity["findings"][0]["severity"] = "critical"
    oversized = deepcopy(raw)
    oversized["findings"][0]["rationale"] = "secret" * 100_000
    for payload in (
        passed_blocking,
        failed_without_blocking,
        severity_contradiction,
        duplicate,
        unknown_severity,
        oversized,
    ):
        with pytest.raises(ProviderInvalidOutputError):
            EditorAgent.validate_output(payload, request=request)


def test_output_rejects_unsafe_control_characters_and_hides_suggested_actions() -> None:
    request = editor_request()
    raw = DeterministicChapterReviewProvider(outcome="warning").editor_sync(request)
    unsafe = deepcopy(raw)
    unsafe["summary"] = "safe\x1bunsafe"
    with pytest.raises(ProviderInvalidOutputError):
        EditorAgent.validate_output(unsafe, request=request)

    secret = "LEAKED_TARGET_PROSE"
    raw["suggested_actions"] = [secret]
    report = EditorAgent.validate_output(raw, request=request)
    assert secret not in repr(report)


def test_public_output_validator_revalidates_constructed_request() -> None:
    bypassed = EditorReviewRequest.model_construct(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=target(project_id=uuid4()),
        approved_outline=outline(),
        contexts=(context(ReviewContextKind.STYLE_GUIDE),),
    )
    raw = DeterministicChapterReviewProvider(outcome="warning").editor_sync(editor_request())
    with pytest.raises(ProviderInvalidOutputError):
        validate_chapter_review_report(
            raw,
            request=bypassed,
            reviewer_role=ReviewerRole.EDITOR,
            mode="chapter_editor",
        )


@pytest.mark.anyio
async def test_agent_boundary_revalidates_constructed_request_and_wrong_mode() -> None:
    bypassed = EditorReviewRequest.model_construct(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        target=target(project_id=uuid4()),
        approved_outline=outline(),
        contexts=(),
    )
    provider = DeterministicChapterReviewProvider()
    with pytest.raises(ProviderConfigurationError):
        await EditorAgent(provider).review(bypassed)
    with pytest.raises(ProviderConfigurationError):
        await EditorAgent(provider).review(chief_request())  # type: ignore[arg-type]


def test_output_boundary_rejects_foreign_models() -> None:
    class PermissiveReport(ChapterReviewReport):
        model_config = ConfigDict(extra="ignore")

    raw = DeterministicChapterReviewProvider().editor_sync(editor_request())
    raw["hidden_authority"] = "database"
    with pytest.raises(ProviderInvalidOutputError):
        EditorAgent.validate_output(PermissiveReport.model_validate(raw), request=editor_request())


class _ExplodingProvider:
    async def review_editor(self, request: object, profile: object) -> object:
        raise RuntimeError("https://provider.invalid sk-secret full chapter body")


class _SafeProvider:
    async def review_editor(self, request: object, profile: object) -> object:
        try:
            raise RuntimeError("leaky provider message")
        except RuntimeError as cause:
            raise ProviderTimeoutError() from cause


@pytest.mark.anyio
async def test_provider_failures_are_safely_normalized() -> None:
    with pytest.raises(ProviderUnavailableError) as unexpected:
        await EditorAgent(_ExplodingProvider()).review(editor_request())
    with pytest.raises(ProviderTimeoutError) as safe:
        await EditorAgent(_SafeProvider()).review(editor_request())
    for error in (unexpected, safe):
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        rendered = repr(error.value) + str(error.value)
        assert "provider.invalid" not in rendered
        assert "sk-secret" not in rendered
        assert "leaky provider message" not in rendered


@pytest.mark.anyio
async def test_fake_uses_no_network_filesystem_credentials_env_time_or_random(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("external or nondeterministic authority was used")

    async def blocked_async(*args: object, **kwargs: object) -> object:
        raise AssertionError("external authority was used")

    profile = ProfileRegistry().load("editor_agent")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked_async)
    monkeypatch.setattr(os, "getenv", blocked)
    monkeypatch.setattr(random, "random", blocked)
    monkeypatch.setattr(time, "time", blocked)
    monkeypatch.setattr(Path, "read_text", blocked)
    monkeypatch.setattr(Path, "write_text", blocked)
    provider = DeterministicChapterReviewProvider(outcome="warning")
    raw = await provider.review_editor(editor_request(), profile)
    assert raw["findings"]


def test_fake_helpers_suppress_constructed_model_serializer_leaks() -> None:
    secret = "SECRET112_CONTEXT"
    request = editor_request()
    context_payload = request.contexts[0].model_dump()
    context_payload["content"] = secret
    constructed_request = EditorReviewRequest.model_construct(
        project_id=request.project_id,
        chapter_id=request.chapter_id,
        workflow_run_id=request.workflow_run_id,
        target=target(project_id=uuid4()),
        approved_outline=request.approved_outline,
        contexts=(context_payload,),
    )
    raw_report = DeterministicChapterReviewProvider(outcome="warning").editor_sync(request)
    raw_report["findings"][0]["rationale"] = secret
    constructed_report = ChapterReviewReport.model_construct(**raw_report)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ProviderConfigurationError):
            DeterministicChapterReviewProvider().editor_sync(constructed_request)
        canonical_review_json_bytes(constructed_report)

    assert secret not in "".join(str(item.message) for item in caught)


@pytest.mark.anyio
async def test_fake_methods_reject_cross_role_request_types() -> None:
    provider = DeterministicChapterReviewProvider()
    with pytest.raises(ProviderConfigurationError):
        provider.editor_sync(chief_request())  # type: ignore[arg-type]
    with pytest.raises(ProviderConfigurationError):
        await provider.review_chief_final(
            editor_request(),  # type: ignore[arg-type]
            ProfileRegistry().load("chief_editor", "chapter_final"),
        )
