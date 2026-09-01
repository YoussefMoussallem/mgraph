"""Test bootstrap + shared fixtures.

The env block below must run before any ``app.*`` import: ``app.config``
requires every key (no defaults), and ``app.main`` builds the app at
import time. ``setdefault`` keeps precedence with anything the caller
already exported (CI can still override).

Two kinds of app are exercised:

* the scaffold's tests (``test_api``, ``test_auth``, ``test_graph``) run in
  ID-token mode with Graph unconfigured, exactly as they shipped;
* the workspace tests (``test_outlook_routes``, ``test_sharepoint_routes``,
  ``test_agent``) use the ``api`` fixture: the real app in access-token mode,
  with a real ``M365Client`` whose credentials are stubbed and whose HTTP
  transport is the mock Graph in ``tests/graph_mock.py``. Every route is
  therefore exercised through the SDKs' own request building, paging and
  error translation — only the tenant is fake.
"""

from __future__ import annotations

import os

TEST_CLIENT_ID = "test-client-id"
TEST_TENANT_ID = "test-tenant-id"

_TEST_ENV = {
    "APP_NAME": "Workspace-Test",
    "APP_ENVIRONMENT": "development",
    "AZURE_CLIENT_ID": TEST_CLIENT_ID,
    "AZURE_TENANT_ID": TEST_TENANT_ID,
    "API_REQUIRED_SCOPE": "",
    "AZURE_CLIENT_SECRET": "",
    "AZURE_CERTIFICATE_PATH": "",
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "",
    "AGENT_MODEL": "",
    "CORS_ALLOW_ORIGINS": "*",
    "LOG_LEVEL": "INFO",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import time
from types import SimpleNamespace

import jwt
import pytest
from app import dependencies, graph
from app.config import Settings
from app.main import create_app
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tests.graph_mock import LOG, make_m365

#: The API scope the workspace tests run under (access-token mode).
SCOPE = "access_as_user"

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

    Extra/overriding claims go in ``claims`` — pass ``{"scp": SCOPE}`` for
    an access token, ``{"exp": ...}`` etc. to break specific validation steps.
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
    """Point token validation at our test keypair instead of Microsoft."""
    stub = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=_PUBLIC_KEY)
    )
    monkeypatch.setattr(dependencies, "_get_jwks_client", lambda: stub)


@pytest.fixture
def reset_settings():
    """Rebuild the Settings singleton around a test's env changes."""
    Settings.reset()
    yield
    Settings.reset()


@pytest.fixture
def workspace_app(monkeypatch: pytest.MonkeyPatch, reset_settings, stub_jwks):
    """The real app in access-token mode, backed by the mock Graph.

    Built outside the lifespan on purpose: the lifespan would construct a
    real ``M365Client`` (and demand a client credential); here the
    process-wide client is the mock-backed one from ``graph_mock``.
    """
    monkeypatch.setenv("API_REQUIRED_SCOPE", SCOPE)
    Settings.reset()
    LOG.clear()
    app = create_app()
    setattr(app.state, graph.STATE_KEY, make_m365())
    return app


@pytest.fixture
def api(workspace_app) -> TestClient:
    """An authenticated client: every request carries an access token for SCOPE."""
    client = TestClient(workspace_app, raise_server_exceptions=False)
    client.headers.update({"Authorization": f"Bearer {make_token(claims={'scp': SCOPE})}"})
    return client
