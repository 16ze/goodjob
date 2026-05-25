from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Configuration minimale pour accéder à Supabase."""

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_service_key: SecretStr = Field(alias="SUPABASE_SERVICE_KEY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class OpenAISettings(DatabaseSettings):
    """Configuration minimale pour les tickets IA sans exiger les integrations futures."""

    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")


class Settings(OpenAISettings):
    """Configuration complète du pipeline validée au démarrage."""

    gmail_client_id: str = Field(alias="GMAIL_CLIENT_ID")
    gmail_client_secret: SecretStr = Field(alias="GMAIL_CLIENT_SECRET")
    gmail_refresh_token: SecretStr = Field(alias="GMAIL_REFRESH_TOKEN")
    notion_token: SecretStr = Field(alias="NOTION_TOKEN")
    notion_database_id: str = Field(alias="NOTION_DATABASE_ID")


class AuthSettings(BaseSettings):
    """Auth dashboard — mot de passe unique + secret JWT."""

    app_password: str = Field(default="", alias="APP_PASSWORD")
    jwt_secret: str = Field(default="dev-secret-changeme-en-prod", alias="JWT_SECRET")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne une configuration singleton pour garder un démarrage déterministe."""

    return Settings()


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    """Retourne les paramètres d'authentification du dashboard."""

    return AuthSettings()


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    """Retourne la configuration Supabase sans exiger les secrets des tickets futurs."""

    return DatabaseSettings()


@lru_cache(maxsize=1)
def get_openai_settings() -> OpenAISettings:
    """Retourne la configuration Supabase + OpenAI sans exiger Gmail ou Notion."""

    return OpenAISettings()
