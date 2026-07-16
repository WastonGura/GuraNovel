from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "GuraNovel API"
    app_env: str = "development"
    app_debug: bool = True
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/guranovel"
    workspace_base_dir: Path = Path(__file__).resolve().parents[2] / "workspaces"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
