"""Application settings.

Every value is read from the environment. Nothing is hardcoded, and the app
refuses to start in production with a placeholder secret.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

_PLACEHOLDER_SECRETS = {
    "change-me",
    "changeme",
    "secret",
    "your-secret-key",
    "dev-secret-change-me",
}


class Settings(BaseSettings):
    """Runtime configuration sourced from the environment."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "Page Pulse"
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = False

    # --- Security ------------------------------------------------------------
    secret_key: str = Field(default="", alias="SECRET_KEY")
    access_token_ttl_minutes: int = Field(default=60 * 24, alias="ACCESS_TOKEN_TTL_MINUTES")
    bcrypt_rounds: int = Field(default=12, alias="BCRYPT_ROUNDS")

    # --- Database ------------------------------------------------------------
    database_url: str = Field(
        default=f"sqlite:///{BASE_DIR / 'pagepulse.db'}",
        alias="DATABASE_URL",
    )

    # --- CORS ----------------------------------------------------------------
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")

    # --- Rate limiting -------------------------------------------------------
    # Set RATE_LIMIT_ENABLED=false only when something in front of the app
    # already limits requests (a proxy, an API gateway) or in tests.
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    auth_rate_limit_per_minute: int = Field(default=10, alias="AUTH_RATE_LIMIT_PER_MINUTE")

    # --- Facebook Graph API --------------------------------------------------
    # Left empty on purpose: the app runs on live Graph data when a token is
    # present, and on clearly-labelled demo data when it is not.
    fb_graph_token: str = Field(default="", alias="FB_GRAPH_TOKEN")
    fb_graph_version: str = Field(default="v21.0", alias="FB_GRAPH_VERSION")
    fb_graph_timeout_seconds: float = Field(default=10.0, alias="FB_GRAPH_TIMEOUT_SECONDS")

    # --- Demo seeding --------------------------------------------------------
    seed_demo_on_startup: bool = Field(default=False, alias="SEED_DEMO_ON_STARTUP")

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        """Reject obviously-fake secrets so a deploy cannot ship one."""
        if value and value.strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "SECRET_KEY looks like a placeholder. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    @property
    def resolved_secret_key(self) -> str:
        """The signing key, generated ephemerally when unset in development."""
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError(
                "SECRET_KEY must be set when ENVIRONMENT=production. Refusing to "
                "start with a generated key, because every restart would silently "
                "invalidate all sessions."
            )
        # Development convenience: a per-process key. Sessions do not survive a
        # restart, which is the honest behaviour rather than a fake fixed key.
        if not hasattr(self, "_ephemeral_secret"):
            object.__setattr__(self, "_ephemeral_secret", secrets.token_urlsafe(48))
        return self._ephemeral_secret

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed CORS allow-list. Empty means same-origin only."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
