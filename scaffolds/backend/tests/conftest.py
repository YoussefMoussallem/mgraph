"""Test bootstrap + shared auth fixtures.

The env block below must run before any ``app.*`` import: ``app.config``
requires every key (no defaults), and ``app.main`` builds the app at
import time. ``setdefault`` keeps precedence with anything the caller
already exported (CI can still override).
"""

from __future__ import annotations

import os

TEST_CLIENT_ID = "test-client-id"
TEST_TENANT_ID = "test-tenant-id"

_TEST_ENV = {
    "APP_NAME": "Scaffold-Test",
    "APP_ENVIRONMENT": "development",
    "AZURE_CLIENT_ID": TEST_CLIENT_ID,
    "AZURE_TENANT_ID": TEST_TENANT_ID,
    "API_REQUIRED_SCOPE": "",
    "AZURE_CLIENT_SECRET": "",
    "AZURE_CERTIFICATE_PATH": "",
    "CORS_ALLOW_ORIGINS": "*",
    "LOG_LEVEL": "INFO",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import time
from types import SimpleNamespace

import jwt
import pytest
from app import dependencies
from app.config import Settings
from cryptography.hazmat.primitives.asymmetric import rsa

# One process-wide keypair — RSA generation is slow, and every test only
# needs *a* consistent signer, not a fresh one.
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


def make_token(
    *,
    audience: str = TEST_CLIENT_ID,
    tenant_id: str = TEST_TENANT_ID,
    expires_in: int = 3600,
    claims: dict | None = None,
) -> str:
    """Mint an RS256 token shaped like an Entra ID token.

    Extra/overriding claims go in ``claims`` — pass ``{"exp": ...}`` etc.
    to break specific validation steps.
    """
    now = int(time.time())
    payload = {
        "aud": audience,
        "iss": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        "iat": now,
        "exp": now + expires_in,
        "sub": "sub-1234",
        "oid": "oid-1234",
        "preferred_username": "user@example.com",
        "name": "Test User",
    }
    payload.update(claims or {})
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256")


@pytest.fixture
def stub_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point token validation at our test keypair instead of Microsoft.

    ``_validate_token`` only uses ``get_signing_key_from_jwt(...).key``,
    so a SimpleNamespace carrying the public key is a faithful stand-in
    for the real ``PyJWKClient``.
    """
    stub = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=_PUBLIC_KEY)
    )
    monkeypatch.setattr(dependencies, "_get_jwks_client", lambda: stub)


@pytest.fixture
def reset_settings():
    """Rebuild the Settings singleton around a test's env changes.

    Use together with ``monkeypatch.setenv`` — reset before the test
    body sees settings, and reset again afterwards so the mutated env
    doesn't leak into the next test's singleton.
    """
    Settings.reset()
    yield
    Settings.reset()
