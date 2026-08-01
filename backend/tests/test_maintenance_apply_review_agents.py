from copy import deepcopy
from pathlib import Path
import socket
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agents import (
    AppliedDocumentReference,
    ApplyChangeRequest,
    ArchivistAgent,
    ConsistencyReviewOutcome,
    DeterministicMaintenanceProvider,
    DocumentVersionReference,
    LoreAgent,
    PostChangeRequest,
    ProfileRegistry,
    ProfileRegistryError,
    RevisionOperation,
    canonical_json_bytes,
)
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderUnavailableError,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
CHANGE_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
OPERATION_ID = UUID("66666666-6666-4666-8666-666666666666")
APPROVAL_ID = UUID("77777777-7777-4777-8777-777777777777")
PLAN_ID = UUID("88888888-8888-4888-8888-888888888888")
PLAN_DOCUMENT_ID = UUID("99999999-9999-4999-8999-999999999999")
PLAN_VERSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
APPLIED_VERSION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def revision_operation(*, operation: str = "revise") -> RevisionOperation:
    return RevisionOperation(
        operation_id=OPERATION_ID,
        sequence=1,
        operation=operation,
        target={"document_id": str(DOCUMENT_ID), "current_version_id": str(VERSION_ID)},
        affected_item_ids=[str(CHANGE_ID)],
        instruction="Prepare a replacement version after approval.",
    )


def apply_request(*, operation: str = "revise") -> ApplyChangeRequest:
    return ApplyChangeRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        approval_id=APPROVAL_ID,
        revision_plan_id=PLAN_ID,
        revision_plan_document_id=PLAN_DOCUMENT_ID,
        revision_plan_version_id=PLAN_VERSION_ID,
        operations=[revision_operation(operation=operation)],
    )


async def apply_output() -> dict[str, object]:
    request = apply_request()
    profile = ProfileRegistry().load("archivist_agent", "apply_change")
    raw = await DeterministicMaintenanceProvider().propose_changes(request, profile)
    assert isinstance(raw, dict)
    return raw


def post_request(*, change_set_id: UUID) -> PostChangeRequest:
    return PostChangeRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        approval_id=APPROVAL_ID,
        revision_plan_id=PLAN_ID,
        revision_plan_document_id=PLAN_DOCUMENT_ID,
        revision_plan_version_id=PLAN_VERSION_ID,
        change_set_id=change_set_id,
        applied_changes=[
            AppliedDocumentReference(
                proposed_edit_id=uuid4(),
                document_id=DOCUMENT_ID,
                previous_version_id=VERSION_ID,
                current_version_id=APPLIED_VERSION_ID,
            )
        ],
    )


@pytest.mark.parametrize(
    ("name", "mode", "schema", "read", "write"),
    [
        (
            "archivist_agent",
            "apply_change",
            "apply_change_output",
            {"maintenance_context", "approved_revision_plan"},
            {"proposed_changes"},
        ),
        (
            "lore_agent",
            "post_change",
            "consistency_review_output",
            {"maintenance_context", "applied_changes"},
            set(),
        ),
    ],
)
def test_new_profiles_have_exact_non_authoritative_permissions(
    name: str, mode: str, schema: str, read: set[str], write: set[str]
) -> None:
    profile = ProfileRegistry().load(name, mode)

    assert profile.output_schema == schema
    assert set(profile.permissions.can_read) == read
    assert set(profile.permissions.can_write) == write
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
        ("archivist_agent", None),
        ("archivist_agent", "post_change"),
        ("lore_agent", "apply_change"),
    ],
)
def test_new_profiles_are_allowlisted_only(name: str, mode: str | None) -> None:
    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry().load(name, mode)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "replacement",
    [
        "    - operations\n    - unexpected_context",
        "    - operations\noptional:\n  - raw_provider_output",
    ],
)
def test_new_profile_context_manifest_rejects_tampering(
    tmp_path: Path, replacement: str
) -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "agents"
        / "profiles"
        / "archivist_apply_change.yaml"
    )
    text = source.read_text().replace("    - operations\n  optional: []", replacement)
    (tmp_path / source.name).write_text(text)

    with pytest.raises(ProfileRegistryError):
        ProfileRegistry(tmp_path).load("archivist_agent", "apply_change")


def test_apply_request_allows_revise_but_fails_closed_for_retire_or_retain_only() -> None:
    assert apply_request().operations[0].operation.value == "revise"
    for operation in ("retire", "retain"):
        with pytest.raises(ValidationError):
            apply_request(operation=operation)


@pytest.mark.anyio
async def test_archivist_proposes_bound_replacement_without_applied_authority() -> None:
    request = apply_request()
    result = await ArchivistAgent(DeterministicMaintenanceProvider()).apply_change(request)
    edit = result.proposed_edits[0]

    assert (
        edit.project_id,
        edit.workflow_run_id,
        edit.change_request_id,
        edit.approval_id,
        edit.revision_plan_id,
        edit.revision_plan_document_id,
        edit.revision_plan_version_id,
        edit.revision_operation_id,
        edit.document_id,
        edit.expected_current_version_id,
        edit.operation.value,
    ) == (
        PROJECT_ID,
        RUN_ID,
        CHANGE_ID,
        APPROVAL_ID,
        PLAN_ID,
        PLAN_DOCUMENT_ID,
        PLAN_VERSION_ID,
        OPERATION_ID,
        DOCUMENT_ID,
        VERSION_ID,
        "replace_content",
    )
    assert "content=" not in repr(edit)
    assert "Proposed maintenance revision" not in repr(edit)
    assert "project_updated" not in type(result).model_fields
    assert "applied" not in type(result).model_fields


@pytest.mark.anyio
async def test_apply_output_validator_requires_request_and_rejects_stale_unknown_or_extra_data() -> None:
    request = apply_request()
    raw = await apply_output()
    with pytest.raises(TypeError):
        ArchivistAgent.validate_output(raw)  # type: ignore[call-arg]

    invalid_payloads: list[dict[str, object]] = []
    for field in (
        "project_id",
        "approval_id",
        "revision_plan_id",
        "revision_plan_document_id",
        "revision_plan_version_id",
    ):
        payload = deepcopy(raw)
        payload[field] = str(uuid4())
        invalid_payloads.append(payload)
    for field in (
        "revision_operation_id",
        "document_id",
        "expected_current_version_id",
    ):
        payload = deepcopy(raw)
        payload["proposed_edits"][0][field] = str(uuid4())  # type: ignore[index]
        invalid_payloads.append(payload)
    unsupported = deepcopy(raw)
    unsupported["proposed_edits"][0]["operation"] = "delete"  # type: ignore[index]
    invalid_payloads.append(unsupported)
    extra = deepcopy(raw)
    extra["proposed_edits"][0]["path"] = "private.md"  # type: ignore[index]
    invalid_payloads.append(extra)

    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError) as error:
            ArchivistAgent.validate_output(payload, request=request)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


@pytest.mark.anyio
async def test_proposed_content_is_verbatim_bounded_and_redacted_from_errors() -> None:
    request = apply_request()
    raw = await apply_output()
    content = "\r\n# Canon\r\n\r\nSee https://example.invalid/path?q=1 and `C:\\story\\note.md`.\r\n"
    raw["proposed_edits"][0]["content"] = content  # type: ignore[index]
    result = ArchivistAgent.validate_output(raw, request=request)
    assert result.proposed_edits[0].content == content
    assert content not in repr(result.proposed_edits[0])

    secret = "sk-not-real-secret full novel body"
    for invalid in (secret + "\x00", "x" * 262_145, "   \r\n\t"):
        payload = deepcopy(raw)
        payload["proposed_edits"][0]["content"] = invalid  # type: ignore[index]
        with pytest.raises(ProviderInvalidOutputError) as error:
            ArchivistAgent.validate_output(payload, request=request)
        rendered = str(error.value) + repr(error.value)
        assert secret not in rendered
        assert invalid[:100] not in rendered


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        ConsistencyReviewOutcome.CLEAN,
        ConsistencyReviewOutcome.WARNING,
        ConsistencyReviewOutcome.BLOCKING,
    ],
)
async def test_post_change_fake_is_byte_stable_for_all_three_outcomes(
    outcome: ConsistencyReviewOutcome,
) -> None:
    provider = DeterministicMaintenanceProvider(outcome)
    change_set = uuid4()
    request = post_request(change_set_id=change_set)
    agent = LoreAgent(provider)

    first = await agent.post_change(request)
    second = await agent.post_change(request)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first.outcome is outcome
    if outcome is ConsistencyReviewOutcome.CLEAN:
        assert first.findings == ()
    else:
        finding = first.findings[0]
        assert finding.blocking is (outcome is ConsistencyReviewOutcome.BLOCKING)
        assert finding.affected_documents == (
            DocumentVersionReference(
                document_id=DOCUMENT_ID, current_version_id=APPLIED_VERSION_ID
            ),
        )


@pytest.mark.anyio
async def test_post_change_rejects_unknown_refs_contradictions_and_self_approval() -> None:
    request = post_request(change_set_id=uuid4())
    profile = ProfileRegistry().load("lore_agent", "post_change")
    raw = await DeterministicMaintenanceProvider(
        ConsistencyReviewOutcome.BLOCKING
    ).review_consistency(request, profile)
    assert isinstance(raw, dict)
    with pytest.raises(TypeError):
        LoreAgent.validate_post_change_output(raw)  # type: ignore[call-arg]

    invalid_payloads = []
    unknown = deepcopy(raw)
    unknown["findings"][0]["affected_documents"][0]["current_version_id"] = str(  # type: ignore[index]
        uuid4()
    )
    invalid_payloads.append(unknown)
    contradiction = deepcopy(raw)
    contradiction["findings"][0]["blocking"] = False  # type: ignore[index]
    invalid_payloads.append(contradiction)
    warning_blocker = deepcopy(raw)
    warning_blocker["outcome"] = "warning"
    invalid_payloads.append(warning_blocker)
    for field in ("project_updated", "corrective_change_approved", "write_document"):
        self_approval = deepcopy(raw)
        self_approval[field] = True
        invalid_payloads.append(self_approval)

    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError) as error:
            LoreAgent.validate_post_change_output(payload, request=request)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


class _ImpactOnlyProvider:
    async def analyze_maintenance_impact(self, request: object, profile: object) -> object:
        return {}


class _PostOnlyProvider:
    async def review_consistency(self, request: object, profile: object) -> object:
        return {}


@pytest.mark.anyio
async def test_new_agent_modes_reject_wrong_provider_capabilities_safely() -> None:
    with pytest.raises(ProviderConfigurationError) as post_error:
        await LoreAgent(_ImpactOnlyProvider()).post_change(post_request(change_set_id=uuid4()))
    with pytest.raises(ProviderConfigurationError) as impact_error:
        await LoreAgent(_PostOnlyProvider()).analyze(object())  # type: ignore[arg-type]
    with pytest.raises(ProviderConfigurationError) as apply_error:
        await ArchivistAgent(_PostOnlyProvider()).apply_change(apply_request())  # type: ignore[arg-type]
    for error in (post_error, impact_error, apply_error):
        assert error.value.__cause__ is None
        assert error.value.__context__ is None


class _ExplodingNewProvider:
    async def propose_changes(self, request: object, profile: object) -> object:
        raise RuntimeError("https://provider.invalid sk-secret full novel body")

    async def review_consistency(self, request: object, profile: object) -> object:
        raise RuntimeError("https://provider.invalid sk-secret full novel body")


@pytest.mark.anyio
async def test_new_agent_paths_reinstantiate_redacted_errors_without_cause_or_context() -> None:
    calls = (
        ArchivistAgent(_ExplodingNewProvider()).apply_change(apply_request()),
        LoreAgent(_ExplodingNewProvider()).post_change(post_request(change_set_id=uuid4())),
    )
    for call in calls:
        with pytest.raises(ProviderUnavailableError) as error:
            await call
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        rendered = str(error.value) + repr(error.value)
        assert "provider.invalid" not in rendered
        assert "sk-secret" not in rendered
        assert "novel body" not in rendered


@pytest.mark.anyio
async def test_new_fakes_make_no_network_or_filesystem_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_profile = ProfileRegistry().load("archivist_agent", "apply_change")
    post_profile = ProfileRegistry().load("lore_agent", "post_change")

    def blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("external authority was used")

    async def blocked_async(*args: object, **kwargs: object) -> object:
        raise AssertionError("external authority was used")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked_async)
    monkeypatch.setattr(Path, "read_text", blocked)
    monkeypatch.setattr(Path, "write_text", blocked)
    provider = DeterministicMaintenanceProvider()
    raw_apply = await provider.propose_changes(apply_request(), apply_profile)
    change_set_id = UUID(raw_apply["change_set_id"])  # type: ignore[arg-type,index]
    raw_post = await provider.review_consistency(
        post_request(change_set_id=change_set_id), post_profile
    )

    assert raw_apply["proposed_edits"]
    assert raw_post["outcome"] == "clean"
