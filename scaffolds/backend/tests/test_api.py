"""Integration tests through the full app — routes, envelope, request id.

Uses the real app from ``app.main`` (conftest's env block ran first) so
middleware order, exception handlers and the auth dependencies are all
exercised exactly as deployed. Only the JWKS fetch is stubbed.
"""

from __future__ import annotations

import pytest
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import make_token


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False lets the catch-all 500 handler run
    # instead of the test re-raising, so we can assert on the envelope.
    return TestClient(app, raise_server_exceptions=False)


def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_me_requires_auth_and_uses_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/me")
    assert res.status_code == 401
    body = res.json()
    assert body["code"] == "unauthorized"
    assert body["detail"] == "Missing or invalid Authorization header"


def test_me_with_valid_token(client: TestClient, stub_jwks) -> None:
    res = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert res.status_code == 200
    assert res.json() == {
        "id": "oid-1234",
        "email": "user@example.com",
        "display_name": "Test User",
    }


def test_foreign_audience_token_rejected(client: TestClient, stub_jwks) -> None:
    # A token minted for a different app registration must not pass.
    res = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {make_token(audience='some-other-app')}"},
    )
    assert res.status_code == 401
    assert res.json()["code"] == "unauthorized"


def test_request_id_minted_and_echoed(client: TestClient) -> None:
    res = client.get("/health")
    assert res.headers.get("X-Request-ID")


def test_request_id_honours_caller_supplied_id(client: TestClient) -> None:
    res = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert res.headers.get("X-Request-ID") == "trace-abc-123"


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.json()["code"] == "not_found"
