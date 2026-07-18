from functools import lru_cache
import ipaddress
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_HOSTNAME_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _is_valid_hostname(hostname: str) -> bool:
    """Accept a DNS name or IP address, without letting malformed authority through."""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.removesuffix(".").split(".")
        return bool(hostname.removesuffix(".")) and all(
            _HOSTNAME_LABEL_PATTERN.fullmatch(label) is not None for label in labels
        )
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True
    )

    app_name: str = "GuraNovel API"
    app_env: str = "development"
    app_debug: bool = True
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/guranovel"
    workspace_base_dir: Path = Path.home() / ".local" / "share" / "guranovel" / "workspaces"
    chapter_generation_provider: Literal["fake", "openai_compatible"] = "fake"
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: SecretStr | None = None
    openai_compatible_model: str | None = None
    openai_compatible_timeout_seconds: float | None = None

    @field_validator("openai_compatible_base_url")
    @classmethod
    def validate_openai_compatible_base_url(cls, value: str | None) -> str | None:
        """Accept only canonical path prefixes that remain stable when an endpoint is appended.

        Percent escapes are rejected altogether so encoded delimiters and traversal cannot be
        reinterpreted by an HTTP client or upstream proxy.
        """
        if value is None:
            return None
        if any(
            character.isspace() or ord(character) <= 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError("must be a safe http or https URL")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError("must be a safe http or https URL") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or not _is_valid_hostname(hostname)
            or "@" in parsed.netloc
            or "?" in value
            or "#" in value
            or "%" in value
            or "\\" in value
            or ";" in parsed.path
            or "//" in parsed.path
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
            or port is not None and not 0 < port <= 65535
        ):
            raise ValueError("must be an http or https URL")
        return value.rstrip("/")

    @field_validator("openai_compatible_model")
    @classmethod
    def validate_openai_compatible_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.llm.contracts import validate_model_identifier

        try:
            return validate_model_identifier(value)
        except ValueError as error:
            raise ValueError("must be a safe model identifier") from error

    @field_validator("openai_compatible_timeout_seconds")
    @classmethod
    def validate_openai_compatible_timeout_seconds(cls, value: float | None) -> float | None:
        if value is not None and not 0 < value <= 120:
            raise ValueError("must be greater than 0 and no more than 120 seconds")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
