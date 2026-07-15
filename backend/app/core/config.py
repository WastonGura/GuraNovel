from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "GuraNovel API"
    app_env: str = "development"
    app_debug: bool = True
    app_version: str = "0.1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
