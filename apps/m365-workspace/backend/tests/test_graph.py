"""The optional Microsoft Graph wiring (app/graph.py).

Skipped when the ``m365`` extra is not installed — the module is optional
and so is its suite. Everything here runs offline: the on-behalf-of exchange
is stood in for by a recording stub, since the SDK's own behaviour is not
what this scaffold is testing.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("m365_client")

from app import graph
from app.middleware import register_exception_handlers
from m365_client import (
    GraphAuthError,
    GraphNotFoundError,
    GraphThrottledError,
    M365AuthError,
    M365Error,
)

from tests.conftest import make_token

SCOPE = "access_as_user"

# Per-test exception for the /boom route, so one app serves every mapping test.
_RAISES: dict[str, BaseException] = {"boom": RuntimeError("unset")}


class _StubM365:
    """Records the on-behalf-of arguments instead of calling Entra."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def graph_for_user(self, assertion: str, user_id: str) -> object:
        self.calls.append((assertion, user_id))
        return object()


def _build_app(m365: Any) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    graph.register_graph_error_handlers(app)
    setattr(app.state, graph.STATE_KEY, m365)

    @app.get("/graph")
    async def graph_route(client: Annotated[object, Depends(graph.get_graph)]) -> dict:
        return {"got_client": client is not None}

    @app.get("/boom")
    async def boom() -> None:
        raise _RAISES["boom"]

    return app


@pytest.fixture
def scope_mode(monkeypatch: pytest.MonkeyPatch, reset_settings) -> None:
    """Access-token mode: the dependency requires ``scp`` to carry SCOPE."""
    monkeypatch.setenv("API_REQUIRED_SCOPE", SCOPE)


def _client(m365: Any) -> TestClient:
    return TestClient(_build_app(m365), raise_server_exceptions=False)


# ── get_graph ────────────────────────────────────────────────────────


def test_get_graph_exchanges_the_callers_own_token(stub_jwks, scope_mode) -> None:
    """The exchange must receive the raw access token and the stable ``oid``.

    Passing ``sub`` instead of ``oid``, or a re-encoded assertion, produces
    cache misses or an outright rejection from Entra — invisible unless the
    arguments themselves are asserted.
    """
    m365 = _StubM365()
    token = make_token(claims={"scp": SCOPE})
    with _client(m365) as client:
        res = client.get("/graph", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200, res.text
    assert res.json() == {"got_client": True}
    assert m365.calls == [(token, "oid-1234")]


def test_id_token_is_rejected_before_any_exchange(stub_jwks, scope_mode) -> None:
    """The failure that trips every team once, caught with a usable message."""
    m365 = _StubM365()
    with _client(m365) as client:
        res = client.get("/graph", headers={"Authorization": f"Bearer {make_token()}"})

    assert res.status_code == 401
    body = res.json()
    assert body["code"] == "unauthorized"
    assert "scp" in body["detail"] and "ID token" in body["detail"]
    assert m365.calls == []


def test_missing_scope_is_403(stub_jwks, scope_mode) -> None:
    with _client(_StubM365()) as client:
        token = make_token(claims={"scp": "some.other.scope"})
        res = client.get("/graph", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"
    assert SCOPE in res.json()["detail"]


def test_unconfigured_graph_is_a_wiring_error_not_a_caller_error(stub_jwks, scope_mode) -> None:
    """No ``M365Client`` on app.state → the catch-all 500, never a 401."""
    with _client(None) as client:
        token = make_token(claims={"scp": SCOPE})
        res = client.get("/graph", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 500
    assert res.json()["code"] == "internal_error"


def test_id_token_mode_cannot_reach_graph(stub_jwks, reset_settings) -> None:
    """Without API_REQUIRED_SCOPE the dependency validated an ID token, which
    cannot be exchanged — a wiring bug, surfaced as a 500."""
    with _client(_StubM365()) as client:
        res = client.get("/graph", headers={"Authorization": f"Bearer {make_token()}"})

    assert res.status_code == 500
    assert res.json()["code"] == "internal_error"


# ── Error handlers ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (GraphAuthError("insufficient privileges"), 403, "graph_forbidden"),
        (GraphNotFoundError("no such message"), 404, "graph_not_found"),
        (GraphThrottledError("slow down"), 429, "graph_throttled"),
        (M365AuthError("no token"), 502, "m365_auth_failed"),
        (M365Error("upstream broke"), 502, "m365_error"),
        (M365Error("conflict", status_code=409), 409, "m365_error"),
    ],
)
def test_typed_errors_map_to_distinct_codes(error: Exception, status: int, code: str) -> None:
    _RAISES["boom"] = error
    with _client(_StubM365()) as client:
        res = client.get("/boom")

    assert res.status_code == status
    assert res.json() == {"code": code, "detail": str(error)}


def test_throttling_propagates_retry_after() -> None:
    _RAISES["boom"] = GraphThrottledError("slow down", retry_after=42)
    with _client(_StubM365()) as client:
        res = client.get("/boom")

    assert res.status_code == 429
    assert res.headers["Retry-After"] == "42"


def test_failed_exchange_carries_entra_diagnostics() -> None:
    """``AADSTS`` codes and the correlation id are what Microsoft support and
    the Entra sign-in logs key on, so they belong in the response."""
    _RAISES["boom"] = M365AuthError(
        "OBO exchange failed", aadsts_code="AADSTS65001", correlation_id="abc-123"
    )
    with _client(_StubM365()) as client:
        detail = client.get("/boom").json()["detail"]

    assert "[code=AADSTS65001]" in detail
    assert "[correlation_id=abc-123]" in detail
