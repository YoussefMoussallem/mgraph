"""Application configuration — grouped, thread-safe singleton.

Copied from the backend scaffold (``scaffolds/backend/app/config.py``) and
extended with one group, ``AgentSettings``, for the assistant's LLM. The
pattern is unchanged:

1. ``_EnvSettings`` is a flat pydantic-settings model whose field names
   match env var keys (and, in dev, the keys in ``.env``). This is the
   only place that reads env vars / .env files and the only place
   secrets exist as plain strings.

2. ``Settings`` reshapes that flat object into typed, frozen
   sub-settings grouped by concern (``settings.azure_ad.client_id``,
   ``settings.agent.model``, etc.). Credentials are wrapped in
   ``SecretStr`` so an accidental ``repr`` / log redacts them; call
   ``.get_secret_value()`` at the site of use only.

Environment-aware behaviour
---------------------------
``APP_ENVIRONMENT`` is the master switch:

* ``production``: ``.env`` files are NEVER loaded — every key must
  arrive as a real environment variable. Production also enforces
  non-empty values for the Azure AD registration and, because this app
  calls Graph, a client credential (see ``_validate_production``).
* ``staging`` / ``development``: ``.env`` is loaded if present next to
  ``pyproject.toml`` (override path with ``APP_ENV_FILE``).

In every environment, every field on ``_EnvSettings`` is required —
no defaults. A missing key crashes the process at boot with a clear
"Field required" error rather than silently falling back.

To add a new setting:
  1. Add a field to ``_EnvSettings`` (matching the env key, no default).
  2. Add it to the relevant frozen sub-settings model.
  3. Wire it through ``Settings.__init__`` from env -> sub-settings.
  4. Add the corresponding entry to ``.env`` and ``.env.example``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _resolve_env_file() -> str | None:
    """Decide which (if any) .env file to feed pydantic-settings.

    Production deliberately ignores .env files entirely — secrets
    arrive as real environment variables. Non-production loads
    ``$APP_ENV_FILE`` if set, else ``.env`` next to ``pyproject.toml``
    if it exists, else nothing.
    """
    env = os.getenv("APP_ENVIRONMENT", "development").lower()
    if env == "production":
        return None
    explicit = os.getenv("APP_ENV_FILE")
    if explicit:
        return explicit
    default = _BACKEND_DIR / ".env"
    return str(default) if default.is_file() else None


class AppSettings(BaseModel, frozen=True):
    """Top-level app identity."""

    name: str
    environment: Environment


class AzureADSettings(BaseModel, frozen=True):
    """Azure AD app registration used for authentication.

    ``required_scope`` selects the token shape ``app.dependencies`` accepts:
    blank validates the Entra ID token; a scope name such as
    ``access_as_user`` requires an access token for this API's own exposed
    scope — what every Graph-backed route in this app needs.
    """

    client_id: str
    tenant_id: str
    required_scope: str


class GraphSettings(BaseModel, frozen=True):
    """Client credential for Microsoft Graph on-behalf-of (``app/graph.py``).

    Exactly one is set when Graph is in use. ``SecretStr`` keeps the secret
    out of any ``repr`` or log line.
    """

    client_secret: SecretStr
    certificate_path: str


class AgentSettings(BaseModel, frozen=True):
    """The assistant's LLM: any OpenAI-compatible chat endpoint.

    ``base_url`` blank means OpenAI itself; set it for Azure OpenAI behind
    a proxy, LiteLLM, or any other compatible gateway. The assistant stays
    disabled — the API answers 503 and the UI says why — until both the key
    and the model are set, so the rest of the app works without an LLM.
    """

    api_key: SecretStr
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key.get_secret_value() and self.model)


class CorsSettings(BaseModel, frozen=True):
    """Allowed CORS origins. ``("*",)`` means all origins (credentials off)."""

    allow_origins: tuple[str, ...]


class LoggingSettings(BaseModel, frozen=True):
    """Log verbosity for the stdlib logging setup."""

    level: LogLevel


# ── Env-file loader (flat keys match env names) ──────────────────────
# Every field is REQUIRED — missing keys fail at boot. Empty values are
# still allowed at parse time (``KEY=`` parses as ``""`` for ``str``
# fields) — ``_validate_production`` catches blank credentials in
# production. ``extra="ignore"`` lets a shared dev .env carry keys for
# sibling services.


class _EnvSettings(BaseSettings):
    """Flat view of every configurable value, sourced from env (+ .env in dev)."""

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App settings
    app_name: str
    app_environment: Environment

    # Azure AD
    azure_client_id: str
    azure_tenant_id: str
    api_required_scope: str

    # Microsoft Graph client credential
    azure_client_secret: str
    azure_certificate_path: str

    # Assistant LLM
    openai_api_key: str
    openai_base_url: str
    agent_model: str

    # CORS
    cors_allow_origins: str

    # Logging
    log_level: LogLevel

    @model_validator(mode="after")
    def _validate_invariants(self) -> _EnvSettings:
        """Cross-field checks that must hold regardless of environment."""
        if self.app_environment == "production":
            self._validate_production()
        return self

    def _validate_production(self) -> None:
        """Refuse to boot in production without required config.

        The dev anonymous-auth fallback in ``app.dependencies`` must never
        be reachable in production, and a Graph-calling app without a
        client credential cannot exchange a single token.
        """
        missing: list[str] = []
        if not self.azure_client_id:
            missing.append("AZURE_CLIENT_ID")
        if not self.azure_tenant_id:
            missing.append("AZURE_TENANT_ID")
        if self.api_required_scope and not (
            self.azure_client_secret or self.azure_certificate_path
        ):
            missing.append("AZURE_CLIENT_SECRET or AZURE_CERTIFICATE_PATH")
        if missing:
            raise ValueError(
                "Production environment is missing required configuration: " + ", ".join(missing)
            )


# ── Thread-safe singleton ─────────────────────────────────────────────


class Settings:
    """Groups all configuration under typed, frozen sub-settings.

    Access via ``Settings.get()`` or the module-level ``get_settings()``.
    Uses double-checked locking so the singleton is safe across threads.
    """

    _instance: Settings | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self, env: _EnvSettings) -> None:
        self.app = AppSettings(
            name=env.app_name,
            environment=env.app_environment,
        )
        self.azure_ad = AzureADSettings(
            client_id=env.azure_client_id,
            tenant_id=env.azure_tenant_id,
            required_scope=env.api_required_scope.strip(),
        )
        self.graph = GraphSettings(
            client_secret=SecretStr(env.azure_client_secret),
            certificate_path=env.azure_certificate_path.strip(),
        )
        self.agent = AgentSettings(
            api_key=SecretStr(env.openai_api_key.strip()),
            base_url=env.openai_base_url.strip(),
            model=env.agent_model.strip(),
        )
        self.cors = CorsSettings(
            allow_origins=tuple(
                origin.strip() for origin in env.cors_allow_origins.split(",") if origin.strip()
            )
            or ("*",),
        )
        self.logging = LoggingSettings(level=env.log_level)

    @classmethod
    def get(cls) -> Settings:
        """Return the process-wide Settings, building it on first access."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(_EnvSettings())
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the cached singleton (useful in tests)."""
        with cls._lock:
            cls._instance = None


def get_settings() -> Settings:
    """Preferred accessor. Thin wrapper around ``Settings.get()``."""
    return Settings.get()
