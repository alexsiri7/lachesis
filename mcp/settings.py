from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    mcp_base_url: str = ""
    secret_key: str = ""

    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_allowed_hosts: str = ""

    github_pat: str = ""

    database_url: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_email: str = ""

    sentry_dsn: str = ""


settings = Settings()
