import json
from pathlib import Path
import socket
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agents import ProfileRegistry, ProfileRegistryError
from app.agents.maintenance_agents import (
    ChiefEditorImpactAgent,
    LoreAgent,
    PlotArchitectAgent,
    WorldbuildingAgent,
)
from app.agents.maintenance_contracts import (
    AffectedItemReference,
    DocumentVersionReference,
    ImpactAffectedItem,
    ImpactLevel,
    ImpactWarning,
    LoreImpactOutput,
    MaintenanceImpactRequest,
    RevisionOperation,
    RevisionPlanOutput,
    RevisionPlanRequest,
    WarningSeverity,
)
from app.agents.maintenance_fakes import DeterministicMaintenanceProvider, canonical_json_bytes
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
CHANGE_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
DOCUMENT_ID_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
VERSION_ID_2 = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
AFFECTED_ID = UUID("66666666-6666-4666-8666-666666666666")
WARNING_ID = UUID("77777777-7777-4777-8777-777777777777")
REQUIREMENT_ID = UUID("88888888-8888-4888-8888-888888888888")
OPERATION_ID = UUID("99999999-9999-4999-8999-999999999999")
PLAN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def document_ref() -> dict[str, str]:
    return {"document_id": str(DOCUMENT_ID), "current_version_id": str(VERSION_ID)}


def second_document_ref() -> dict[str, str]:
    return {"document_id": str(DOCUMENT_ID_2), "current_version_id": str(VERSION_ID_2)}


def impact_item() -> dict[str, object]:
    return {
        "stable_reference": "world/core-rule",
        "item_type": "world",
        "impact_level": "high",
        "document": document_ref(),
        "reason": "The rule is referenced by later planning artifacts.",
    }


def persisted_item() -> dict[str, object]:
    return {**impact_item(), "affected_item_id": str(AFFECTED_ID)}


def warning(*, severity: str = "advisory") -> dict[str, object]:
    return {
        "warning_id": str(WARNING_ID),
        "severity": severity,
        "code": "reader_expectation_risk",
        "message": "The change may weaken an established expectation.",
        "affected_item_references": ["world/core-rule"],
    }


def lore_output(*, safe_to_change: bool = True, severity: str = "advisory") -> dict[str, object]:
    return {
        "affected_items": [impact_item()],
        "impact_summary": "One canonical world document is affected.",
        "required_rewrites": [
            {
                "requirement_id": str(REQUIREMENT_ID),
                "affected_item_reference": "world/core-rule",
                "document": document_ref(),
                "instruction": "Revise the rule explanation while preserving history.",
            }
        ],
        "safe_to_change": safe_to_change,
        "warnings": [warning(severity=severity)],
    }


def revision_output() -> dict[str, object]:
    return {
        "plan_id": str(PLAN_ID),
        "summary": "Revise the affected canonical document in sequence.",
        "operations": [
            {
                "operation_id": str(OPERATION_ID),
                "sequence": 1,
                "operation": "revise",
                "target": document_ref(),
                "affected_item_ids": [str(AFFECTED_ID)],
                "instruction": "Prepare a new version while preserving the current version.",
            }
        ],
        "safety": {
            "requires_user_confirmation": True,
            "preserve_existing_versions": True,
            "direct_write_authority": False,
        },
        "warnings": [],
    }


def impact_request() -> MaintenanceImpactRequest:
    return MaintenanceImpactRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        change_request="Adjust the canonical rule without rewriting history.",
        document_refs=[DocumentVersionReference(**document_ref())],
    )


def revision_request() -> RevisionPlanRequest:
    return RevisionPlanRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        change_request="Adjust the canonical rule without rewriting history.",
        affected_items=[AffectedItemReference.model_validate(persisted_item())],
        document_refs=[DocumentVersionReference(**document_ref())],
    )


@pytest.mark.parametrize(
    ("name", "mode", "schema"),
    [
        ("lore_agent", "maintenance_impact", "lore_maintenance_impact_output"),
        ("chief_editor", "maintenance_impact", "chief_editor_maintenance_impact_output"),
        ("plot_architect_agent", "revision_plan", "revision_plan_output"),
        ("worldbuilding_agent", "revision_plan", "revision_plan_output"),
    ],
)
def test_registry_loads_only_exact_agent_mode_pairs(name: str, mode: str, schema: str) -> None:
    profile = ProfileRegistry().load(name, mode=mode)

    assert profile.name == name
    assert profile.mode == mode
    assert profile.output_schema == schema
    assert profile.model.response_format == "json_schema"
    assert "network" in profile.permissions.cannot
    assert "credentials" in profile.permissions.cannot


def test_registry_profiles_match_the_exact_permission_manifest() -> None:
    expected = {
        ("concept_agent", None): (
            {"project_creation_context"},
            {"pitch/concept_options.md"},
            {"network", "credentials"},
        ),
        ("chief_editor", None): (
            {"pitch/concept_options.md"},
            set(),
            {"pitch/selected_concept.md"},
        ),
        ("lore_agent", "maintenance_impact"): (
            {"maintenance_context", "document_refs"},
            set(),
            {"network", "credentials", "document_versions"},
        ),
        ("chief_editor", "maintenance_impact"): (
            {"maintenance_context", "document_refs"},
            set(),
            {"network", "credentials", "document_versions"},
        ),
        ("plot_architect_agent", "revision_plan"): (
            {"maintenance_context", "document_refs"},
            {"revision_plan"},
            {"network", "credentials", "document_versions"},
        ),
        ("worldbuilding_agent", "revision_plan"): (
            {"maintenance_context", "document_refs"},
            {"revision_plan"},
            {"network", "credentials", "document_versions"},
        ),
    }
    for pair, permission_sets in expected.items():
        profile = ProfileRegistry().load(*pair)
        assert (
            set(profile.permissions.can_read),
            set(profile.permissions.can_write),
            set(profile.permissions.cannot),
        ) == permission_sets


@pytest.mark.parametrize(
    "tamper",
    [
        lambda text: text.replace("name: lore_agent", "name: chief_editor"),
        lambda text: text.replace("mode: maintenance_impact", "mode: revision_plan"),
        lambda text: text.replace("agent_role: lore_agent", "agent_role: worldbuilding_agent"),
        lambda text: text.replace(
            "output_schema: lore_maintenance_impact_output", "output_schema: revision_plan_output"
        ),
        lambda text: text.replace("  can_write: []", "  can_write:\n    - revision_plan"),
        lambda text: text.replace(
            "    - document_refs\n  can_write", "    - document_refs\n    - revision_plan\n  can_write"
        ),
        lambda text: text.replace("    - credentials\n", ""),
        lambda text: text.replace("    - credentials\n", "    - credentials\n    - credentials\n"),
    ],
)
def test_registry_rejects_manifest_identity_and_permission_tampering(
    tmp_path: Path, tamper: object
) -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "agents"
        / "profiles"
        / "lore_maintenance_impact.yaml"
    )
    tampered = tamper(source.read_text())  # type: ignore[operator]
    (tmp_path / source.name).write_text(tampered)

    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry(tmp_path).load("lore_agent", "maintenance_impact")
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("lore_agent", None),
        ("lore_agent", "revision_plan"),
        ("chief_editor", "post_change"),
        ("../lore_agent", "maintenance_impact"),
        ("plot_architect_agent/../../secret", "revision_plan"),
    ],
)
def test_registry_rejects_unknown_mode_and_path_injection(name: str, mode: str | None) -> None:
    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry().load(name, mode=mode)
    assert error.value.details is None
    assert "lore_agent" not in str(error.value)
    assert "secret" not in str(error.value)


def test_existing_profiles_keep_their_original_serialized_shape() -> None:
    concept = ProfileRegistry().load("concept_agent")
    chief = ProfileRegistry().load("chief_editor")

    assert "mode" not in concept.model_dump()
    assert "mode" not in chief.model_dump()
    assert concept.output_schema == "concept_generation_output"
    assert chief.output_schema == "chief_editor_review_output"
    assert concept.model.model == "concept-model-v1"
    assert chief.model.model == "deterministic-chief-editor-v1"


def test_contracts_are_strict_frozen_and_accept_only_canonical_non_nil_uuid_references() -> None:
    ref = DocumentVersionReference(**document_ref())
    assert ref.document_id == DOCUMENT_ID
    with pytest.raises(ValidationError):
        ref.document_id = uuid4()  # type: ignore[misc]

    for invalid in (
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        str(UUID(int=0)),
        7,
        True,
    ):
        with pytest.raises(ValidationError):
            DocumentVersionReference(
                document_id=invalid,  # type: ignore[arg-type]
                current_version_id=str(VERSION_ID),
            )
    with pytest.raises(ValidationError):
        DocumentVersionReference.model_validate({**document_ref(), "path": "C:/private.txt"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("item_type", "location"),
        ("impact_level", "critical"),
        ("extra", "raw model content"),
    ],
)
def test_impact_output_rejects_unknown_fields_item_types_and_levels(
    field: str, value: object
) -> None:
    payload = lore_output()
    if field == "extra":
        payload[field] = value
    else:
        payload["affected_items"][0][field] = value  # type: ignore[index]
    with pytest.raises(ProviderInvalidOutputError) as error:
        LoreAgent.validate_output(payload)
    assert error.value.details is None
    assert "raw model content" not in str(error.value)


def test_impact_contract_rejects_duplicates_dangling_refs_and_warning_contradictions() -> None:
    duplicate = lore_output()
    duplicate["affected_items"] = [impact_item(), impact_item()]
    dangling = lore_output()
    dangling["warnings"][0]["affected_item_references"] = ["world/unknown"]  # type: ignore[index]
    unsafe_without_blocker = lore_output(safe_to_change=False)
    safe_with_blocker = lore_output(safe_to_change=True, severity="blocking")

    for payload in (duplicate, dangling, unsafe_without_blocker, safe_with_blocker):
        with pytest.raises(ProviderInvalidOutputError):
            LoreAgent.validate_output(payload)


def test_non_document_affected_items_are_valid_but_stable_references_are_strict_and_unique() -> None:
    payload = lore_output()
    payload["affected_items"][0]["document"] = None  # type: ignore[index]
    payload["required_rewrites"] = []
    result = LoreAgent.validate_output(payload, request=impact_request())
    assert result.affected_items[0].document is None
    assert result.affected_items[0].stable_reference == "world/core-rule"

    invalid_payloads = []
    for stable_reference in (
        "",
        "../secret",
        "/world/rule",
        "world/../rule",
        "world/core/rule",
        "world\\rule",
        "world/rule?secret",
        "x" * 129,
    ):
        invalid = lore_output()
        invalid["affected_items"][0]["stable_reference"] = stable_reference  # type: ignore[index]
        invalid_payloads.append(invalid)
    duplicate = lore_output()
    second = impact_item()
    duplicate["affected_items"] = [impact_item(), second]
    invalid_payloads.append(duplicate)

    for invalid in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError):
            LoreAgent.validate_output(invalid)


@pytest.mark.anyio
async def test_revision_fake_plans_for_non_document_affected_item() -> None:
    item = persisted_item()
    item["document"] = None
    request = RevisionPlanRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        change_request="Adjust a character fact that affects a canonical document.",
        affected_items=[AffectedItemReference.model_validate(item)],
        document_refs=[DocumentVersionReference(**document_ref())],
    )
    result = await PlotArchitectAgent(DeterministicMaintenanceProvider()).plan(request)

    assert result.operations[0].affected_item_ids == (AFFECTED_ID,)
    assert result.operations[0].target.current_version_id == VERSION_ID


@pytest.mark.anyio
async def test_impact_fake_supports_a_non_document_change_without_document_context() -> None:
    request = MaintenanceImpactRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        change_request="Rename a character relationship that has no document yet.",
        document_refs=[],
    )
    result = await LoreAgent(DeterministicMaintenanceProvider()).analyze(request)

    assert len(result.affected_items) == 1
    assert result.affected_items[0].document is None
    assert result.required_rewrites == ()


def test_chief_editor_contract_requires_typed_reader_and_commercial_impact() -> None:
    payload = {
        **lore_output(),
        "reader_expectation_impact": "high",
        "commercial_impact": "medium",
    }
    result = ChiefEditorImpactAgent.validate_output(payload, request=impact_request())
    assert result.reader_expectation_impact is ImpactLevel.HIGH
    assert result.commercial_impact is ImpactLevel.MEDIUM

    for field, value in (
        ("reader_expectation_impact", "critical"),
        ("commercial_impact", "unknown"),
    ):
        malformed = dict(payload)
        malformed[field] = value
        with pytest.raises(ProviderInvalidOutputError):
            ChiefEditorImpactAgent.validate_output(malformed, request=impact_request())


def test_outputs_fail_closed_against_request_document_versions() -> None:
    mismatched = lore_output()
    mismatched["affected_items"][0]["document"]["current_version_id"] = str(uuid4())  # type: ignore[index]
    with pytest.raises(ProviderInvalidOutputError):
        LoreAgent.validate_output(mismatched, request=impact_request())

    plan = revision_output()
    plan["operations"][0]["target"]["current_version_id"] = str(uuid4())  # type: ignore[index]
    with pytest.raises(ProviderInvalidOutputError):
        PlotArchitectAgent.validate_output(plan, request=revision_request())


def test_rewrite_and_revision_targets_remain_bound_to_their_affected_items() -> None:
    impact = lore_output()
    impact["required_rewrites"][0]["document"] = second_document_ref()  # type: ignore[index]
    request = MaintenanceImpactRequest(
        **{
            **impact_request().model_dump(),
            "document_refs": [
                DocumentVersionReference(**document_ref()),
                DocumentVersionReference(**second_document_ref()),
            ],
        }
    )
    with pytest.raises(ProviderInvalidOutputError):
        LoreAgent.validate_output(impact, request=request)

    revision = revision_output()
    revision["operations"][0]["target"] = second_document_ref()  # type: ignore[index]
    revision_input = RevisionPlanRequest(
        **{
            **revision_request().model_dump(),
            "document_refs": [
                DocumentVersionReference(**document_ref()),
                DocumentVersionReference(**second_document_ref()),
            ],
        }
    )
    with pytest.raises(ProviderInvalidOutputError):
        PlotArchitectAgent.validate_output(revision, request=revision_input)


def test_non_document_items_may_bind_only_to_an_allowlisted_revision_target() -> None:
    item = persisted_item()
    item["document"] = None
    request = RevisionPlanRequest(
        project_id=PROJECT_ID,
        workflow_run_id=RUN_ID,
        change_request_id=CHANGE_ID,
        change_request="Update the non-document relationship.",
        affected_items=[AffectedItemReference.model_validate(item)],
        document_refs=[DocumentVersionReference(**second_document_ref())],
    )
    payload = revision_output()
    payload["operations"][0]["target"] = second_document_ref()  # type: ignore[index]
    assert PlotArchitectAgent.validate_output(payload, request=request).operations[0].target == (
        DocumentVersionReference(**second_document_ref())
    )

    payload["operations"][0]["target"] = document_ref()  # type: ignore[index]
    with pytest.raises(ProviderInvalidOutputError):
        PlotArchitectAgent.validate_output(payload, request=request)


def test_requests_reject_duplicate_document_refs_and_hide_transient_change_text() -> None:
    with pytest.raises(ValidationError):
        MaintenanceImpactRequest(
            project_id=PROJECT_ID,
            workflow_run_id=RUN_ID,
            change_request_id=CHANGE_ID,
            change_request="sensitive novel premise",
            document_refs=[
                DocumentVersionReference(**document_ref()),
                DocumentVersionReference(**document_ref()),
            ],
        )
    request = impact_request()
    assert "Adjust the canonical rule" not in repr(request)

    with pytest.raises(ValidationError) as validation_error:
        MaintenanceImpactRequest(
            project_id=PROJECT_ID,
            workflow_run_id=RUN_ID,
            change_request_id=CHANGE_ID,
            change_request="sk-not-a-real-secret\x00",
            document_refs=[DocumentVersionReference(**document_ref())],
        )
    assert "sk-not-a-real-secret" not in str(validation_error.value)


class _MalformedProvider:
    async def analyze_maintenance_impact(self, request: object, profile: object) -> object:
        return {"raw_output": "https://provider.invalid sk-not-a-real-key raw novel text"}

    async def plan_revision(self, request: object, profile: object) -> object:
        return {"raw_output": "https://provider.invalid sk-not-a-real-key raw novel text"}


@pytest.mark.anyio
async def test_missing_profile_stays_safe(tmp_path: Path) -> None:
    with pytest.raises(ProfileRegistryError) as profile_error:
        await LoreAgent(DeterministicMaintenanceProvider(), ProfileRegistry(tmp_path)).analyze(
            impact_request()
        )
    assert profile_error.value.code == "agent_profile_unavailable"
    assert str(tmp_path) not in str(profile_error.value)
    assert profile_error.value.__cause__ is None
    assert profile_error.value.__context__ is None


def test_revision_plan_rejects_unknown_operations_paths_content_and_write_authority() -> None:
    valid = revision_output()
    assert RevisionPlanOutput.model_validate(valid).safety.direct_write_authority is False

    invalid_payloads: list[dict[str, object]] = []
    for key, value in (
        ("operation", "overwrite"),
        ("path", "/private/novel.md"),
        ("content", "full novel text"),
    ):
        payload = revision_output()
        payload["operations"][0][key] = value  # type: ignore[index]
        invalid_payloads.append(payload)
    authority = revision_output()
    authority["safety"]["direct_write_authority"] = True  # type: ignore[index]
    invalid_payloads.append(authority)
    path_in_instruction = revision_output()
    path_in_instruction["operations"][0]["instruction"] = "Write C:\\private\\novel.md"  # type: ignore[index]
    invalid_payloads.append(path_in_instruction)

    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError):
            PlotArchitectAgent.validate_output(payload)


@pytest.mark.parametrize(
    "path_like",
    [
        "Read (/etc/passwd).",
        'Read "/etc/passwd".',
        "Write (C:\\private\\novel.md).",
        "Use \\\\server\\share.",
        "Use \\\\?\\C:\\secret.",
        "Use ~/secret.",
        "Use relative/private.md.",
        "Use ../secret.",
        "Use file?token=value.",
        "Use file#fragment.",
        "Use NUL.",
        "Open secrets.txt.",
        "Use %2e%2e%2fsecret.",
        "Use https:%2F%2Fprovider.invalid%3Ftoken%3Dsecret.",
        "Fetch MAILTO:author@example.invalid.",
        "Load file:secrets.txt.",
        "Fetch gopher:provider.invalid.",
        "Use %u002e%u002e%u002fsecret.",
        "Open secrets.log.",
        "Fetch abcdefghijklmnopqrstuvwxyzabcdefg:payload.",
    ],
)
def test_all_planning_text_fields_reject_path_like_values(path_like: str) -> None:
    impact_summary = lore_output()
    impact_summary["impact_summary"] = path_like
    rewrite_instruction = lore_output()
    rewrite_instruction["required_rewrites"][0]["instruction"] = path_like  # type: ignore[index]
    plan_summary = revision_output()
    plan_summary["summary"] = path_like
    operation_instruction = revision_output()
    operation_instruction["operations"][0]["instruction"] = path_like  # type: ignore[index]

    for payload in (impact_summary, rewrite_instruction):
        with pytest.raises(ProviderInvalidOutputError):
            LoreAgent.validate_output(payload)
    for payload in (plan_summary, operation_instruction):
        with pytest.raises(ProviderInvalidOutputError):
            PlotArchitectAgent.validate_output(payload)


@pytest.mark.parametrize(
    "safe_prose",
    [
        "Version 1.0 remains compatible.",
        "Note: revise canon after user confirmation.",
        "Use e.g. clearer wording for the revision.",
    ],
)
def test_planning_text_allows_normal_version_and_colon_prose(safe_prose: str) -> None:
    impact_summary = lore_output()
    impact_summary["impact_summary"] = safe_prose
    rewrite_instruction = lore_output()
    rewrite_instruction["required_rewrites"][0]["instruction"] = safe_prose  # type: ignore[index]
    plan_summary = revision_output()
    plan_summary["summary"] = safe_prose
    operation_instruction = revision_output()
    operation_instruction["operations"][0]["instruction"] = safe_prose  # type: ignore[index]

    LoreAgent.validate_output(impact_summary)
    LoreAgent.validate_output(rewrite_instruction)
    PlotArchitectAgent.validate_output(plan_summary)
    PlotArchitectAgent.validate_output(operation_instruction)


def test_revision_plan_rejects_bool_as_sequence_and_duplicate_or_unknown_refs() -> None:
    invalid_payloads = []
    bool_sequence = revision_output()
    bool_sequence["operations"][0]["sequence"] = True  # type: ignore[index]
    invalid_payloads.append(bool_sequence)
    duplicate_operation = revision_output()
    duplicate_operation["operations"] = [
        revision_output()["operations"][0],
        revision_output()["operations"][0],
    ]
    invalid_payloads.append(duplicate_operation)
    unknown_affected = revision_output()
    unknown_affected["operations"][0]["affected_item_ids"] = [str(uuid4())]  # type: ignore[index]
    invalid_payloads.append(unknown_affected)

    request = revision_request()
    for payload in invalid_payloads:
        with pytest.raises(ProviderInvalidOutputError):
            PlotArchitectAgent.validate_output(payload, request=request)


@pytest.mark.anyio
async def test_deterministic_fake_outputs_canonical_byte_stable_results_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_socket(*args: object, **kwargs: object) -> object:
        raise AssertionError("fake attempted socket access")

    async def blocked_async_http(*args: object, **kwargs: object) -> object:
        raise AssertionError("fake attempted async HTTP access")

    def blocked_http(*args: object, **kwargs: object) -> object:
        raise AssertionError("fake attempted HTTP access")

    monkeypatch.setattr(socket, "create_connection", blocked_socket)
    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked_async_http)
    monkeypatch.setattr(httpx.Client, "request", blocked_http)
    provider = DeterministicMaintenanceProvider()
    lore_agent = LoreAgent(provider)
    chief_agent = ChiefEditorImpactAgent(provider)
    plot_agent = PlotArchitectAgent(provider)
    world_agent = WorldbuildingAgent(provider)

    first_lore = await lore_agent.analyze(impact_request())
    second_lore = await lore_agent.analyze(impact_request())
    first_chief = await chief_agent.analyze(impact_request())
    second_chief = await chief_agent.analyze(impact_request())
    first_plot = await plot_agent.plan(revision_request())
    second_plot = await plot_agent.plan(revision_request())
    first_world = await world_agent.plan(revision_request())
    second_world = await world_agent.plan(revision_request())

    assert canonical_json_bytes(first_lore) == canonical_json_bytes(second_lore)
    assert canonical_json_bytes(first_chief) == canonical_json_bytes(second_chief)
    assert canonical_json_bytes(first_plot) == canonical_json_bytes(second_plot)
    assert canonical_json_bytes(first_world) == canonical_json_bytes(second_world)
    assert "affected_item_id" not in first_lore.model_dump_json()
    assert canonical_json_bytes(first_plot) == json.dumps(
        first_plot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


@pytest.mark.anyio
async def test_fake_impact_references_do_not_collide_between_change_requests() -> None:
    provider = DeterministicMaintenanceProvider()
    first = await LoreAgent(provider).analyze(impact_request())
    second_request = MaintenanceImpactRequest(
        **{**impact_request().model_dump(), "change_request_id": uuid4()}
    )
    second = await LoreAgent(provider).analyze(second_request)

    assert first.affected_items[0].stable_reference != second.affected_items[0].stable_reference


class _ExplodingProvider:
    async def analyze_maintenance_impact(self, request: object, profile: object) -> object:
        raise RuntimeError(
            "https://provider.invalid/v1 sk-not-a-real-key full prompt and raw novel text"
        )

    async def plan_revision(self, request: object, profile: object) -> object:
        raise RuntimeError(
            "https://provider.invalid/v1 sk-not-a-real-key full prompt and raw novel text"
        )


class _SafeErrorProvider:
    def __init__(self, error_type: type[Exception]) -> None:
        self._error_type = error_type

    async def analyze_maintenance_impact(self, request: object, profile: object) -> object:
        try:
            raise RuntimeError("sk-not-a-real-key malicious provider cause")
        except RuntimeError as cause:
            raise self._error_type() from cause

    async def plan_revision(self, request: object, profile: object) -> object:
        try:
            raise RuntimeError("sk-not-a-real-key malicious provider cause")
        except RuntimeError as cause:
            raise self._error_type() from cause


async def _call_agent(agent_kind: str, provider: object) -> object:
    if agent_kind == "lore":
        return await LoreAgent(provider).analyze(impact_request())  # type: ignore[arg-type]
    if agent_kind == "chief_editor":
        return await ChiefEditorImpactAgent(provider).analyze(impact_request())  # type: ignore[arg-type]
    if agent_kind == "plot_architect":
        return await PlotArchitectAgent(provider).plan(revision_request())  # type: ignore[arg-type]
    return await WorldbuildingAgent(provider).plan(revision_request())  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize("agent_kind", ["lore", "chief_editor", "plot_architect", "worldbuilding"])
async def test_all_agent_paths_normalize_malformed_output_without_context(agent_kind: str) -> None:
    with pytest.raises(ProviderInvalidOutputError) as error:
        await _call_agent(agent_kind, _MalformedProvider())
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    rendered = repr(error.value) + str(error.value)
    for secret in ("provider.invalid", "sk-not-a-real-key", "raw novel text"):
        assert secret not in rendered


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("agent_kind", "error_type"),
    [
        (agent_kind, error_type)
        for agent_kind in ("lore", "chief_editor", "plot_architect", "worldbuilding")
        for error_type in (
            ProviderConfigurationError,
            ProviderInvalidOutputError,
            ProviderRateLimitedError,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )
    ],
)
async def test_agent_preserves_only_existing_safe_provider_errors(
    agent_kind: str,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type) as error:
        await _call_agent(agent_kind, _SafeErrorProvider(error_type))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert error.value.details is None  # type: ignore[attr-defined]
    assert "malicious provider cause" not in repr(error.value)


@pytest.mark.anyio
@pytest.mark.parametrize("agent_kind", ["lore", "chief_editor", "plot_architect", "worldbuilding"])
async def test_unexpected_provider_errors_are_redacted(agent_kind: str) -> None:
    with pytest.raises(ProviderUnavailableError) as error:
        await _call_agent(agent_kind, _ExplodingProvider())
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    rendered = repr(error.value) + str(error.value)
    for secret in ("provider.invalid", "sk-not-a-real-key", "full prompt", "raw novel text"):
        assert secret not in rendered


def test_contract_objects_expose_no_provider_or_persistence_authority_fields() -> None:
    allowed = {
        "affected_items",
        "impact_summary",
        "required_rewrites",
        "safe_to_change",
        "warnings",
    }
    result = LoreImpactOutput.model_validate(lore_output())
    assert set(result.model_dump()) == allowed
    assert "affected_item_id" not in ImpactAffectedItem.model_fields
    assert "affected_item_id" in AffectedItemReference.model_fields
    assert set(RevisionOperation.model_fields) == {
        "operation_id",
        "sequence",
        "operation",
        "target",
        "affected_item_ids",
        "instruction",
    }
    assert set(ImpactWarning.model_fields) == {
        "warning_id",
        "severity",
        "code",
        "message",
        "affected_item_references",
    }
    assert WarningSeverity.BLOCKING.value == "blocking"
    assert ImpactLevel.HIGH.value == "high"
