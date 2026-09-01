"""Build :class:`M365Settings` from environment variables.

The core package deliberately never reads the environment on its own -- a
library that silently consumes ``os.environ`` is hard to test and hides where
secrets enter the process. But every consuming service was then writing the
same forty lines to map the same env var names onto ``M365Settings``, which is
its own kind of duplication.

This module is the compromise: an *explicit*, opt-in helper. Nothing here runs
unless a service calls it, so the no-implicit-env property holds, while the
mapping itself lives in one place.

Recognised variables:

===============================  ===========================================
``AZURE_TENANT_ID``              required
``AZURE_CLIENT_ID``              required
``AZURE_CLIENT_SECRET``          one of secret / certificate required
``AZURE_CERTIFICATE_PATH``       "
``M365_TIMEOUT_SECONDS``         optional, default 30
``M365_MAX_RETRIES``             optional, default 3
``M365_CACHE_MAX_ENTRIES``       optional, default 500
``M365_CACHE_TTL_SECONDS``       optional, default 3000
===============================  ===========================================

Loading a ``.env`` file is left to the caller (e.g. ``python-dotenv``) so the
SDK never touches the filesystem behind a service's back.
"""

from __future__ import annotations

import os

from .config import CacheSettings, M365Settings
from .errors import M365ConfigError

__all__ = [
    "env_flag",
    "require_env",
    "settings_from_env",
]

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def env_flag(key: str, *, default: bool = False) -> bool:
    """Read a boolean environment variable.

    Accepts ``1/true/yes/on`` (case-insensitive) as true; any other non-empty
    value is false. An unset or blank variable yields ``default``.
    """
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE_VALUES


def require_env(key: str, *, hint: str = "") -> str:
    """Return a required environment variable, or fail with a usable message.

    Raises:
        M365ConfigError: The variable is unset or blank. Raised at startup so
            a misconfigured deployment fails immediately rather than on the
            first request that happens to need the value.
    """
    value = os.getenv(key, "").strip()
    if not value:
        message = f"{key} is required but is not set."
        if hint:
            message = f"{message} {hint}"
        raise M365ConfigError(message)
    return value


def _env_int(key: str, default: int) -> int:
    """Parse an integer env var, naming the offender if it is not a number."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise M365ConfigError(f"{key} must be an integer, got {raw!r}") from None


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise M365ConfigError(f"{key} must be a number, got {raw!r}") from None


def settings_from_env(*, hint: str = "") -> M365Settings:
    """Build :class:`M365Settings` from the environment.

    Args:
        hint: Appended to the error message when a required variable is
            missing -- use it to name the service's own setup step, e.g.
            ``"Copy .env.example to .env and fill it in."``

    Raises:
        M365ConfigError: A required variable is missing, or a numeric one is
            unparseable. ``M365Settings`` itself raises the same type if the
            credential combination is invalid (neither secret nor certificate,
            or both).
    """
    return M365Settings(
        tenant_id=require_env("AZURE_TENANT_ID", hint=hint),
        client_id=require_env("AZURE_CLIENT_ID", hint=hint),
        client_secret=os.getenv("AZURE_CLIENT_SECRET") or None,
        certificate_path=os.getenv("AZURE_CERTIFICATE_PATH") or None,
        timeout_seconds=_env_float("M365_TIMEOUT_SECONDS", 30.0),
        max_retries=_env_int("M365_MAX_RETRIES", 3),
        cache=CacheSettings(
            max_entries=_env_int("M365_CACHE_MAX_ENTRIES", 500),
            ttl_seconds=_env_int("M365_CACHE_TTL_SECONDS", 3000),
        ),
    )
