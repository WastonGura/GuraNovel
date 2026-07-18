import httpx
import pytest
from pydantic import ValidationError

from app.api.deps import get_chapter_generation_composition
from app.core.config import Settings
from app.llm import (
    ChapterGenerationProvenance,
    FakeChapterGenerationProvider,
    ProviderConfigurationError,
)


def test_fake_configuration_needs_no_real_provider_values() -> None:
    composition = get_chapter_generation_composition(Settings())
    assert isinstance(composition.provider, FakeChapterGenerationProvider)
    assert composition.provenance.provider_kind == "fake"


def test_real_configuration_is_lazy_but_fails_closed_when_constructed() -> None:
    settings = Settings(chapter_generation_provider="openai_compatible")
    with pytest.raises(ProviderConfigurationError) as error:
        get_chapter_generation_composition(settings)
    assert error.value.code == "provider_configuration_error"
    assert "OPENAI_COMPATIBLE" not in str(error.value)


def test_real_configuration_accepts_openai_compatible_model_and_owns_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.openai_compatible_provider.httpx.AsyncClient", lambda **_: object()
    )
    settings = Settings(
        chapter_generation_provider="openai_compatible",
        openai_compatible_base_url="https://provider.test/v1",
        openai_compatible_api_key="secret",
        openai_compatible_model="vendor/model",
        openai_compatible_timeout_seconds=30,
    )
    composition = get_chapter_generation_composition(settings)
    assert composition.provider.model == "vendor/model"  # type: ignore[attr-defined]
    assert composition.provenance.model_identifier == "vendor/model"


@pytest.mark.parametrize(
    "model",
    [
        " ",
        "model name",
        "vendor/model\nnext",
        "https://provider.test/model",
        "Authorization:***",
        "X-Api-Key:***",
        "redacted",
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-test-abc",
        "sk_abc",
        "api-key-secret",
        "a" * 129,
    ],
)
def test_rejects_unsafe_openai_compatible_model_identifiers(model: str) -> None:
    with pytest.raises(ValidationError):
        Settings(openai_compatible_model=model)
    with pytest.raises(ValueError):
        ChapterGenerationProvenance("openai_compatible", model, "v1")


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://provider.test",
        "provider.test",
        "https://provider.test:bad",
        "https://user:embedded-secret@provider.test/v1",
        "https://provider.test/v1?token=embedded-secret",
        "https://provider.test/v1#fragment",
        "https:///v1",
        "https://provider.test%2Fevil/v1",
        "https://provider.test/v1;params",
        "https://provider.test/with space",
    ],
)
def test_rejects_non_http_openai_compatible_url(base_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(openai_compatible_base_url=base_url)


@pytest.mark.parametrize("whitespace", ["\u0085", "\u00a0", "\u2003", "\u2028"])
@pytest.mark.parametrize(
    "base_url_template",
    [
        "https://provider.test/v1{whitespace}adjacent-secret",
        "https://provider{whitespace}adjacent-secret.test/v1",
    ],
)
def test_rejects_unicode_whitespace_in_openai_compatible_urls_without_leaking_secrets(
    whitespace: str, base_url_template: str
) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(openai_compatible_base_url=base_url_template.format(whitespace=whitespace))
    assert "adjacent-secret" not in str(error.value)


def test_canonicalizes_openai_compatible_url_path_prefix() -> None:
    settings = Settings(openai_compatible_base_url="https://provider.test/v1/")
    assert settings.openai_compatible_base_url == "https://provider.test/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://provider.test/v1/../evil",
        "https://provider.test/v1/./evil",
        "https://provider.test/v1/%2e%2e/evil",
        "https://provider.test/v1/%2E/evil",
        "https://provider.test/v1/%2f/evil",
        "https://provider.test/v1/%5C/evil",
        r"https://provider.test/v1\evil",
        "https://provider.test/v1//evil",
        "https://provider.test/v1%3Ftoken%3Dembedded-secret",
    ],
)
def test_rejects_noncanonical_openai_compatible_url_path_prefixes(base_url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(openai_compatible_base_url=base_url)
    assert "embedded-secret" not in str(error.value)


def test_composition_normalizes_client_construction_failure_to_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        chapter_generation_provider="openai_compatible",
        openai_compatible_base_url="https://provider.test/v1",
        openai_compatible_api_key="secret-that-must-not-leak",
        openai_compatible_model="vendor/model",
        openai_compatible_timeout_seconds=30,
    )

    def fail_client_construction(*args: object, **kwargs: object) -> httpx.AsyncClient:
        raise httpx.InvalidURL("opaque invalid URL detail")

    monkeypatch.setattr("app.llm.openai_compatible_provider.httpx.AsyncClient", fail_client_construction)
    with pytest.raises(ProviderConfigurationError) as error:
        get_chapter_generation_composition(settings)

    assert error.value.code == "provider_configuration_error"
    assert "secret-that-must-not-leak" not in str(error.value)
    assert "opaque invalid URL detail" not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, 121])
def test_rejects_unbounded_openai_compatible_timeout(timeout: int) -> None:
    with pytest.raises(ValidationError):
        Settings(openai_compatible_timeout_seconds=timeout)
