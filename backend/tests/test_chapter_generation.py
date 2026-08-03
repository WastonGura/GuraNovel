import dataclasses
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.deps import get_chapter_generation_composition
from app.core.config import Settings
from app.llm import (
    ChapterGenerationProvider,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
    ChapterGenerationProvenance,
    FakeChapterGenerationProvider,
    ProviderInvalidOutputError,
    ProviderConfigurationError,
    ProviderUnavailableError,
    validate_chapter_generation_response,
    validate_chapter_generation_output,
)
from app.production.fake_generator import FakeChapterGenerator
from app.services.chapter_production_service import ChapterProductionService


class _EvilInt(int):
    def __repr__(self) -> str:
        return "evil-usage-secret"


@pytest.mark.anyio
async def test_fake_provider_implements_contract_with_fixture_equivalent_artifacts() -> None:
    request = ChapterGenerationRequest(
        project_title="The Glass Archive", chapter_number=7, title="The Locked Door"
    )

    provider = FakeChapterGenerationProvider()
    response = await provider.generate(request)
    expected = FakeChapterGenerator().generate("The Glass Archive", 7, "The Locked Door")

    assert isinstance(provider, ChapterGenerationProvider)
    assert isinstance(response, ChapterGenerationResponse)
    assert isinstance(response.result, ChapterGenerationResult)
    assert response.result.outline.encode("utf-8") == expected.outline.encode("utf-8")
    assert response.result.draft.encode("utf-8") == expected.draft.encode("utf-8")
    assert response.result.summary.encode("utf-8") == expected.summary.encode("utf-8")
    assert response.input_tokens is None
    assert response.output_tokens is None


@pytest.mark.parametrize("chapter_number", [0, -1, 1.5, True, "1", None])
def test_chapter_generation_request_rejects_non_positive_integer_chapter_numbers(
    chapter_number: object,
) -> None:
    with pytest.raises(ValueError, match="chapter_number must be a positive integer"):
        ChapterGenerationRequest("The Glass Archive", chapter_number, "A Title")  # type: ignore[arg-type]


def test_chapter_generation_request_and_result_are_immutable() -> None:
    request = ChapterGenerationRequest("The Glass Archive", 7, "The Locked Door")
    result = ChapterGenerationResult(outline="outline", draft="draft", summary="summary")
    provenance = ChapterGenerationProvenance("fake", "deterministic-fake-v1", "v1")

    assert dataclasses.is_dataclass(request)
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.title = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.draft = "Changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        provenance.model_identifier = "Changed"  # type: ignore[misc]


def test_raw_chapter_output_conversion_returns_typed_artifacts() -> None:
    result = validate_chapter_generation_output(
        {"outline": "outline", "draft": "draft", "summary": "summary"}
    )

    assert result == ChapterGenerationResult("outline", "draft", "summary")


@pytest.mark.parametrize(
    "raw_output",
    [
        None,
        [],
        {"outline": "outline", "draft": "draft"},
        {"outline": "outline", "draft": "draft", "summary": "summary", "extra": "no"},
        {"outline": 1, "draft": "draft", "summary": "summary"},
        {"outline": "", "draft": "draft", "summary": "summary"},
        {"outline": "outline", "draft": "", "summary": "summary"},
        {"outline": "outline", "draft": "draft", "summary": ""},
    ],
)
def test_raw_chapter_output_conversion_normalizes_all_invalid_payloads(
    raw_output: object,
) -> None:
    with pytest.raises(ProviderInvalidOutputError) as error:
        validate_chapter_generation_output(raw_output)

    assert error.value.code == "provider_invalid_output"
    assert error.value.message == "The generation provider returned invalid output."
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_legacy_chapter_validation_does_not_chain_raw_content() -> None:
    raw_secret = "raw-provider-secret"
    with pytest.raises(ProviderInvalidOutputError) as output_error:
        validate_chapter_generation_output(
            {"outline": raw_secret, "draft": "draft", "summary": 7}
        )
    assert output_error.value.__cause__ is None
    assert output_error.value.__context__ is None
    assert raw_secret not in repr(output_error.value)

    response = ChapterGenerationResponse(ChapterGenerationResult(raw_secret, "draft", "summary"))
    object.__setattr__(response, "input_tokens", raw_secret)
    with pytest.raises(ProviderInvalidOutputError) as response_error:
        validate_chapter_generation_response(response)
    assert response_error.value.__cause__ is None
    assert response_error.value.__context__ is None
    assert raw_secret not in repr(response_error.value)


@pytest.mark.parametrize("model_identifier", ["vendor/model", "provider:model"])
def test_server_owned_provenance_accepts_standard_openai_compatible_model_identifiers(
    model_identifier: str,
) -> None:
    provenance = ChapterGenerationProvenance("openai_compatible", model_identifier, "v1")
    assert provenance.to_payload()["model_identifier"] == model_identifier


@pytest.mark.parametrize(
    "args",
    [
        ("", "model", "v1"),
        ("fake", "", "v1"),
        ("fake", "model", ""),
        ("fake", "https://model.example", "v1"),
        ("fake prompt: write a chapter", "model", "v1"),
        ("fake", "Authorization: Bearer secret", "v1"),
        ("fake", "X-Api-Key:opaque-secret", "v1"),
        ("fake", "redacted", "v1"),
        ("fake", "api_key=super-secret", "v1"),
        ("fake", "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789", "v1"),
        ("fake", "sk-test-abc", "v1"),
        ("fake", "sk_abc", "v1"),
        ("fake", "vendor/model\nnext", "v1"),
        ("fake", "a" * 129, "v1"),
    ],
)
def test_server_owned_provenance_rejects_invalid_identifiers(args: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        ChapterGenerationProvenance(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize("counter", [-1, True, 1_000_000_001, "12", _EvilInt(12)])
def test_response_validation_rejects_unbounded_or_non_integer_counters(counter: object) -> None:
    response = ChapterGenerationResponse(
        result=ChapterGenerationResult("outline", "draft", "summary"), output_tokens=0
    )
    object.__setattr__(response, "input_tokens", counter)

    with pytest.raises(ProviderInvalidOutputError):
        validate_chapter_generation_response(response)
    assert "evil-usage-secret" not in repr(response)


def test_provenance_rejects_integer_subclasses_without_repr_leakage() -> None:
    provenance = ChapterGenerationProvenance("fake", "safe-model", "v1")

    with pytest.raises(ValueError) as error:
        provenance.to_payload(input_tokens=_EvilInt(7))

    assert "evil-usage-secret" not in repr(error.value)


@pytest.mark.parametrize(
    "identifiers",
    [
        ("fake_sk-prod", "safe-model", "v1"),
        ("fake", "vendor/sk-proj-safe-looking", "v1"),
        ("fake", "vendor.sk-prod", "v1"),
        ("fake", "vendor/token/model", "v1"),
        ("fake", "vendor.secret/model", "v1"),
        ("fake", "safe-model", "v1_sk-prod"),
        ("fake", "safe-model", "v1.token-prod"),
    ],
)
def test_provenance_rejects_sensitive_material_in_any_identifier_segment(
    identifiers: tuple[str, str, str],
) -> None:
    with pytest.raises(ValueError):
        ChapterGenerationProvenance(*identifiers)


def test_provenance_payload_revalidates_identifiers_and_config_rejects_nested_sk_segment() -> None:
    provenance = ChapterGenerationProvenance("fake", "safe-model", "v1")
    object.__setattr__(provenance, "model_identifier", "vendor/sk-proj-mutated")

    with pytest.raises(ValueError):
        provenance.to_payload()
    with pytest.raises(ValueError):
        Settings(_env_file=None, openai_compatible_model="vendor/sk-proj-config")


def test_legacy_response_requires_exact_envelopes() -> None:
    class ResultSubclass(ChapterGenerationResult):
        pass

    class ResponseSubclass(ChapterGenerationResponse):
        pass

    with pytest.raises(ProviderInvalidOutputError) as response_error:
        validate_chapter_generation_response(
            ResponseSubclass(ChapterGenerationResult("o", "d", "s"))
        )
    with pytest.raises(ProviderInvalidOutputError) as result_error:
        validate_chapter_generation_response(
            ChapterGenerationResponse(ResultSubclass("o", "d", "s"))
        )

    for error in (response_error.value, result_error.value):
        assert type(error) is ProviderInvalidOutputError
        assert error.__cause__ is None
        assert error.__context__ is None


def test_legacy_response_normalizes_malicious_getters_and_error_subclasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "legacy-getter-secret"

    class UnsafeInvalidOutput(ProviderInvalidOutputError):
        def __init__(self) -> None:
            self.secret = secret
            super().__init__()

    response = ChapterGenerationResponse(ChapterGenerationResult("o", "d", "s"))
    original_getattribute = ChapterGenerationResponse.__getattribute__

    def malicious_getattribute(self: object, name: str) -> object:
        if name == "result":
            raise UnsafeInvalidOutput()
        return original_getattribute(self, name)

    monkeypatch.setattr(
        ChapterGenerationResponse,
        "__getattribute__",
        malicious_getattribute,
    )
    with pytest.raises(ProviderInvalidOutputError) as error:
        validate_chapter_generation_response(response)

    assert type(error.value) is ProviderInvalidOutputError
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in repr(error.value)


def test_response_has_no_provenance_field_and_ignores_injected_one() -> None:
    response = ChapterGenerationResponse(ChapterGenerationResult("outline", "draft", "summary"))
    object.__setattr__(response, "provenance", "sk-proj-opaque-provider-secret")

    validated = validate_chapter_generation_response(response)

    assert not hasattr(validated, "provenance")


class _ProviderFailureSession:
    def __init__(self, rollback_error: Exception | None = None) -> None:
        self.rollback_error = rollback_error
        self.rollback_calls = 0

    async def scalar(self, _: object) -> None:
        return None

    async def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error


async def _provider_failure_service(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: BaseException,
    *,
    rollback_error: Exception | None = None,
) -> tuple[ChapterProductionService, _ProviderFailureSession]:
    class FailingProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            raise provider_error

    session = _ProviderFailureSession(rollback_error)
    service = ChapterProductionService(
        session,  # type: ignore[arg-type]
        generation_provider=FailingProvider(),
        generation_provenance=ChapterGenerationProvenance("test", "test-model", "v1"),
    )

    async def scoped_chapter(*_: object) -> object:
        return SimpleNamespace(
            project=SimpleNamespace(title="Project"), chapter_number=1, title="Chapter"
        )

    async def lock_chapter(*_: object) -> None:
        return None

    monkeypatch.setattr(service, "_scoped_chapter", scoped_chapter)
    monkeypatch.setattr(service, "_lock_chapter", lock_chapter)
    return service, session


@pytest.mark.parametrize(
    ("provider_error", "expected_type"),
    [
        (ProviderUnavailableError(), ProviderUnavailableError),
        (ValueError("raw-provider-secret"), ProviderInvalidOutputError),
        (RuntimeError("raw-provider-secret"), ProviderUnavailableError),
    ],
)
@pytest.mark.anyio
async def test_chapter_service_normalizes_provider_failures_after_rollback(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: BaseException,
    expected_type: type[Exception],
) -> None:
    service, session = await _provider_failure_service(monkeypatch, provider_error)

    with pytest.raises(expected_type) as error:
        await service.start_production(uuid4(), uuid4())

    assert type(error.value) is expected_type
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "raw-provider-secret" not in repr(error.value)
    assert session.rollback_calls == 1


@pytest.mark.anyio
async def test_chapter_service_normalizer_never_reads_untrusted_exception_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "hostile-service-class-secret"

    class HostileUnavailable(ProviderUnavailableError):
        def __getattribute__(self, name: str) -> object:
            if name == "__class__":
                raise RuntimeError(secret)
            return super().__getattribute__(name)

    service, session = await _provider_failure_service(
        monkeypatch,
        HostileUnavailable(),
    )

    with pytest.raises(ProviderUnavailableError) as error:
        await service.start_production(uuid4(), uuid4())

    assert type(error.value) is ProviderUnavailableError
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in repr(error.value)
    assert session.rollback_calls == 1


@pytest.mark.anyio
async def test_chapter_service_does_not_chain_rollback_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session = await _provider_failure_service(
        monkeypatch,
        ProviderUnavailableError(),
        rollback_error=RuntimeError("rollback-secret"),
    )

    with pytest.raises(ProviderUnavailableError) as error:
        await service.start_production(uuid4(), uuid4())

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "rollback-secret" not in repr(error.value)
    assert session.rollback_calls == 1


@pytest.mark.anyio
async def test_chapter_service_rolls_back_and_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session = await _provider_failure_service(
        monkeypatch, asyncio.CancelledError("cancel-secret")
    )

    with pytest.raises(asyncio.CancelledError):
        await service.start_production(uuid4(), uuid4())

    assert session.rollback_calls == 1


def test_composition_configuration_failure_has_no_raw_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_secret = "raw-client-construction-secret"

    def fail_provider(**_: object) -> object:
        raise RuntimeError(raw_secret)

    monkeypatch.setattr(
        "app.api.deps.OpenAICompatibleChapterGenerationProvider",
        fail_provider,
    )
    configured_settings = SimpleNamespace(
        chapter_generation_provider="openai_compatible",
        openai_compatible_base_url="https://provider.test/v1",
        openai_compatible_api_key=SimpleNamespace(get_secret_value=lambda: "private-key"),
        openai_compatible_model="safe-model",
        openai_compatible_timeout_seconds=30,
    )

    with pytest.raises(ProviderConfigurationError) as error:
        get_chapter_generation_composition(configured_settings)

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert raw_secret not in repr(error.value)
