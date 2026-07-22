from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents import (
    ConceptAgent,
    ConceptAgentRequest,
    ConceptGenerationOutput,
    ConceptProvider,
    ProfileRegistry,
    ProfileRegistryError,
)
from app.llm import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderUnavailableError,
)


def valid_output() -> dict[str, object]:
    return {
        "options": [
            {
                "id": "glass-archive",
                "title": "The Glass Archive",
                "logline": "A young archivist must recover a stolen memory before it rewrites her city.",
                "premise": "In a city that stores memories in glass, an apprentice finds a conspiracy.",
                "genres": ["fantasy", "mystery"],
            }
        ]
    }


def valid_request() -> ConceptAgentRequest:
    return ConceptAgentRequest(
        project_id=uuid4(),
        workflow_run_id=None,
        user_seed="A city that stores memories in glass.",
        target_platform="web novel",
        preferred_genres=["fantasy"],
        disliked_elements=["grimdark"],
    )


def test_concept_profile_loads_only_allowlisted_fields() -> None:
    profile = ProfileRegistry().load("concept_agent")

    assert profile.name == "concept_agent"
    assert profile.agent_role == "concept_agent"
    assert profile.model.provider == "openai_compatible"
    assert profile.model.model == "concept-model-v1"
    assert profile.model.response_format == "json_schema"
    assert profile.permissions.can_write == ["pitch/concept_options.md"]
    assert profile.output_schema == "concept_generation_output"


@pytest.mark.parametrize(
    "body",
    [
        "name: concept_agent\nversion: v1\ndescription: x\nagent_role: concept_agent\nurl: https://private\n",
        "name: concept_agent\nversion: v1\ndescription: x\nagent_role: concept_agent\nmodel: {provider: openai_compatible, model: test, temperature: '0.4'}\npermissions: {can_read: [], can_write: [], cannot: []}\ncontext_policy: {required: [], optional: [], max_context_tokens: 1}\nsystem_prompt: x\noutput_schema: concept_generation_output\n",
        "name: concept_agent\nversion: v1\ndescription: x\nagent_role: concept_agent\nmodel: {provider: openai_compatible, model: test, temperature: 1, top_p: 0.9, max_tokens: 10, response_format: json}\npermissions: {can_read: [], can_write: [], cannot: []}\ncontext_policy: {required: [], optional: [], max_context_tokens: 1}\nsystem_prompt: x\noutput_schema: concept_generation_output\n",
        "name: concept_agent\nversion: v1\ndescription: x\nagent_role: concept_agent\nmodel: {provider: openai_compatible, model: test, api_key: not-a-real-secret, temperature: 0.4, top_p: 0.9, max_tokens: 10, response_format: json}\npermissions: {can_read: [], can_write: [], cannot: []}\ncontext_policy: {required: [], optional: [], max_context_tokens: 1}\nsystem_prompt: x\noutput_schema: concept_generation_output\n",
    ],
)
def test_profile_rejects_unknown_sensitive_and_non_strict_fields(tmp_path: Path, body: str) -> None:
    (tmp_path / "concept.yaml").write_text(body)

    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry(tmp_path).load("concept_agent")

    assert str(error.value) == "The requested agent profile is unavailable."
    assert "private" not in str(error.value)
    assert "not-a-real-secret" not in str(error.value)


def test_profile_missing_and_unrecognized_fail_closed_without_path_details(tmp_path: Path) -> None:
    for name in ("missing", "../concept_agent"):
        with pytest.raises(ProfileRegistryError) as error:
            ProfileRegistry(tmp_path).load(name)
        assert error.value.details is None
        assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "untrusted_provider"),
        ("response_format", "json"),
    ],
)
def test_profile_rejects_unallowlisted_structured_model_configuration(
    tmp_path: Path, field: str, value: str
) -> None:
    body = Path(__file__).parents[1] / "app" / "agents" / "profiles" / "concept.yaml"
    profile_text = body.read_text().replace(f"  {field}: " + {
        "provider": "openai_compatible",
        "model": "concept-model-v1",
        "response_format": "json_schema",
    }[field], f"  {field}: {value}")
    (tmp_path / "concept.yaml").write_text(profile_text)

    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry(tmp_path).load("concept_agent")

    assert error.value.details is None
    assert str(error.value) == "The requested agent profile is unavailable."


def test_profile_accepts_safe_future_model_identifier_but_rejects_sensitive_model_config(
    tmp_path: Path,
) -> None:
    profile_path = Path(__file__).parents[1] / "app" / "agents" / "profiles" / "concept.yaml"
    future_model = profile_path.read_text().replace("concept-model-v1", "vendor/future-model-v2")
    (tmp_path / "concept.yaml").write_text(future_model)

    assert ProfileRegistry(tmp_path).load("concept_agent").model.model == "vendor/future-model-v2"

    (tmp_path / "concept.yaml").write_text(
        future_model.replace("vendor/future-model-v2", "sk-not-a-real-provider-secret")
    )
    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry(tmp_path).load("concept_agent")
    assert error.value.details is None
    assert "not-a-real-provider-secret" not in str(error.value)

    (tmp_path / "concept.yaml").write_text(
        future_model.replace(
            "  model: vendor/future-model-v2",
            "  model: vendor/future-model-v2\n  headers: {Authorization: Bearer not-a-real-secret}",
        )
    )
    with pytest.raises(ProfileRegistryError) as error:
        ProfileRegistry(tmp_path).load("concept_agent")
    assert error.value.details is None
    assert "not-a-real-secret" not in str(error.value)


def test_concept_request_and_output_are_strict_and_round_trip() -> None:
    request = valid_request()
    output = ConceptGenerationOutput.model_validate(valid_output())

    assert request.model_dump()["user_seed"] == "A city that stores memories in glass."
    assert output.options[0].id == "glass-archive"
    with pytest.raises(ValidationError):
        ConceptAgentRequest.model_validate({**request.model_dump(), "extra": "no"})
    with pytest.raises(ValidationError):
        ConceptAgentRequest.model_validate({**request.model_dump(), "preferred_genres": "fantasy"})
    with pytest.raises(ValidationError):
        ConceptGenerationOutput.model_validate(
            {"options": [valid_output()["options"][0], valid_output()["options"][0]]}
        )


def test_concept_request_target_platform_accepts_explicit_or_omitted_none_only_when_absent() -> None:
    request = valid_request()
    explicit_none = ConceptAgentRequest.model_validate(
        {**request.model_dump(), "target_platform": None}
    )
    omitted = ConceptAgentRequest.model_validate(
        {key: value for key, value in request.model_dump().items() if key != "target_platform"}
    )

    assert explicit_none.target_platform is None
    assert explicit_none.model_dump()["target_platform"] is None
    assert omitted.target_platform is None
    assert omitted.model_dump()["target_platform"] is None
    assert (
        ConceptAgentRequest.model_validate(
            {**request.model_dump(), "target_platform": "x" * 500}
        ).target_platform
        == "x" * 500
    )
    for invalid in ("", "x" * 501, 7, ["web novel"]):
        with pytest.raises(ValidationError):
            ConceptAgentRequest.model_validate({**request.model_dump(), "target_platform": invalid})


@pytest.mark.parametrize(
    "raw",
    [
        {"options": []},
        {"options": [{**valid_output()["options"][0], "id": "bad id"}]},
        {"options": [{**valid_output()["options"][0], "extra": "secret upstream output"}]},
        {"options": "not-a-list"},
    ],
)
def test_untrusted_output_is_normalized_to_safe_error(raw: object) -> None:
    with pytest.raises(ProviderInvalidOutputError) as error:
        ConceptAgent.validate_output(raw)
    assert str(error.value) == "The generation provider returned invalid output."
    assert "secret upstream output" not in str(error.value)


class _ResultProvider:
    async def generate_concepts(self, request: ConceptAgentRequest, profile: object) -> object:
        return valid_output()


class _BrokenProvider:
    async def generate_concepts(self, request: ConceptAgentRequest, profile: object) -> object:
        raise RuntimeError("provider secret detail")


class _ConfigurationErrorProvider:
    async def generate_concepts(self, request: ConceptAgentRequest, profile: object) -> object:
        raise ProviderConfigurationError()


@pytest.mark.anyio
async def test_concept_agent_validates_provider_result_and_maps_unexpected_failures() -> None:
    agent = ConceptAgent(_ResultProvider())
    assert isinstance(_ResultProvider(), ConceptProvider)
    assert (await agent.generate(valid_request())).options[0].title == "The Glass Archive"

    with pytest.raises(ProviderUnavailableError) as error:
        await ConceptAgent(_BrokenProvider()).generate(valid_request())
    assert "provider secret detail" not in str(error.value)


@pytest.mark.anyio
async def test_concept_agent_preserves_safe_provider_configuration_failure() -> None:
    with pytest.raises(ProviderConfigurationError) as error:
        await ConceptAgent(_ConfigurationErrorProvider()).generate(valid_request())

    assert error.value.code == "provider_configuration_error"
    assert error.value.message == "The generation provider is not configured. Please contact the service operator."
    assert error.value.__cause__ is None
