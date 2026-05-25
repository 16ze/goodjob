from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration validée au démarrage depuis l'environnement."""

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_service_key: SecretStr = Field(alias="SUPABASE_SERVICE_KEY")
    anthropic_api_key: SecretStr = Field(alias="ANTHROPIC_API_KEY")
    gmail_client_id: str = Field(alias="GMAIL_CLIENT_ID")
    gmail_client_secret: SecretStr = Field(alias="GMAIL_CLIENT_SECRET")
    gmail_refresh_token: SecretStr = Field(alias="GMAIL_REFRESH_TOKEN")
    notion_token: SecretStr = Field(alias="NOTION_TOKEN")
    notion_database_id: str = Field(alias="NOTION_DATABASE_ID")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne une configuration singleton pour garder un démarrage déterministe."""

    return Settings()
