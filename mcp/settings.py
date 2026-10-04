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

    claude_usd_per_million_tokens: float = 5.0

    sentry_dsn: str = ""

    @property
    def sqlalchemy_database_url(self) -> str:
        # SQLAlchemy picks the driver for a driver-less postgres URL itself, and
        # 2.1 switched that default from psycopg2 to psycopg (v3), which isn't installed.
        for scheme in ("postgresql://", "postgres://"):
            if self.database_url.startswith(scheme):
                return "postgresql+psycopg2://" + self.database_url.removeprefix(scheme)
        return self.database_url


settings = Settings()
