"""Microsoft Graph on behalf of the caller — the wiring around ``m365-client``.

Only for apps that call Graph. It needs the ``m365`` extra
(``pip install -e ".[test,m365]"``); delete this module and the extra if your
app does not call Graph, and nothing else in the scaffold notices.

What lives here, and why here rather than in the SDK
----------------------------------------------------
``m365-client`` owns the Graph side: the on-behalf-of exchange, credential
caching, the configured client, the error taxonomy. Everything that touches
the *HTTP* side of a service is this module's job, because it is the same
for every FastAPI app and different for every framework:

1. ``lifespan`` — one ``M365Client`` per process, built at startup and
   closed at shutdown. Per-request construction throws away the credential
   cache that makes the delegated path affordable; skipping ``close()``
   leaks credential and HTTP sessions.
2. ``get_graph`` — the dependency that turns the authenticated caller into
   a ``GraphServiceClient`` acting *as that user*, so Graph enforces their
   own permissions and application code is not the gate.
3. ``register_graph_error_handlers`` — the SDK's typed errors rendered as
   the platform envelope, with the diagnostics a caller can act on
   (``Retry-After`` on a throttle, the ``AADSTS`` code on a failed exchange).

Route handlers should let SDK errors propagate rather than catching and
re-raising them as ``HTTPException``: the handlers here map each typed error
to the right status and a distinct ``code``, which is strictly more
information than a status-derived string.

Prerequisites outside the code — the part that trips every team once: the
app registration exposes an API scope, the Graph *delegated* permissions are
admin-consented, and the caller sends an **access token** for that scope,
never an ID token. ``API_REQUIRED_SCOPE`` in ``.env`` switches
``app.dependencies`` into that mode; this module refuses to start without it.

Usage::

    # app/main.py
    from app import graph
    app = FastAPI(title=..., lifespan=graph.lifespan)
    graph.register_graph_error_handlers(app)     # after register_exception_handlers

    # a route
    from app.graph import get_graph
    from outlook_client import OutlookClient

    @router.get("/messages")
    async def messages(client: Annotated[GraphServiceClient, Depends(get_graph)]):
        return await OutlookClient(client).list_messages(top=10)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from m365_client import (
    GraphAuthError,
    GraphNotFoundError,
    GraphThrottledError,
    M365AuthError,
    M365Client,
    M365Error,
    M365Settings,
)
from msgraph import GraphServiceClient

from app.config import get_settings
from app.dependencies import CurrentUser, get_current_user
from app.logging_setup import get_logger

log = get_logger(__name__)

#: ``app.state`` attribute holding the process-wide ``M365Client``.
STATE_KEY = "m365"


# ── Lifecycle ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the process-wide ``M365Client`` at startup; close it at shutdown.

    Pass as ``FastAPI(lifespan=lifespan)``. Under the dev fallback (Azure
    registration unset) no client is built and ``get_graph`` fails loudly
    instead, so the rest of the app still runs without a tenant.

    Raises at boot, not on the first request, when the configuration cannot
    work: no ``API_REQUIRED_SCOPE`` (an ID token cannot be exchanged), or no
    client credential (``M365Settings`` insists on one, because the
    on-behalf-of exchange is a confidential-client operation).
    """
    settings = get_settings()
    client: M365Client | None = None

    if settings.azure_ad.client_id and settings.azure_ad.tenant_id:
        if not settings.azure_ad.required_scope:
            raise RuntimeError(
                "app.graph needs API_REQUIRED_SCOPE: the on-behalf-of exchange takes an "
                "access token for this API's own scope, and an ID token cannot be exchanged."
            )
        client = M365Client(
            M365Settings(
                tenant_id=settings.azure_ad.tenant_id,
                client_id=settings.azure_ad.client_id,
                client_secret=settings.graph.client_secret.get_secret_value() or None,
                certificate_path=settings.graph.certificate_path or None,
            )
        )
        log.info("m365_client ready (base_url=%s)", client.base_url)
    else:
        log.warning(
            "AZURE_CLIENT_ID / AZURE_TENANT_ID unset: Microsoft Graph is unavailable "
            "(dev fallback)"
        )

    setattr(app.state, STATE_KEY, client)
    try:
        yield
    finally:
        if client is not None:
            await client.close()
            log.info("m365_client closed")


# ── Dependency ───────────────────────────────────────────────────────


async def get_graph(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> GraphServiceClient:
    """A Graph client acting as the authenticated caller (on-behalf-of).

    Handlers get a ready client and never see token exchange, credential
    caching, or MSAL. The SDK caches credentials per user, so the exchange
    round trip only happens on a cache miss.

    ``M365AuthError`` — the exchange failing at Entra — propagates to the
    handler registered below, which renders a 502: a failed exchange is a
    configuration or consent problem on our side, not something the caller
    can fix by signing in again.

    The two ``RuntimeError``s are wiring bugs, not caller errors; they
    surface as the catch-all 500 with the reason in the log.
    """
    m365: M365Client | None = getattr(request.app.state, STATE_KEY, None)
    if m365 is None:
        raise RuntimeError(
            "Microsoft Graph is not configured: set AZURE_CLIENT_ID, AZURE_TENANT_ID and a "
            "client credential, and pass app.graph.lifespan to FastAPI(...)."
        )
    if user.assertion is None:
        raise RuntimeError(
            "get_graph needs an access token for this API's scope; set API_REQUIRED_SCOPE "
            "so app.dependencies validates one instead of the ID token."
        )
    return await m365.graph_for_user(user.assertion, user.user_id)


# ── Error handlers ───────────────────────────────────────────────────


def _envelope(status: int, code: str, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "detail": detail}, headers=headers)


def register_graph_error_handlers(app: FastAPI) -> None:
    """Render the SDK's typed errors as the platform envelope.

    Register after ``register_exception_handlers``. Starlette dispatches on
    the most specific registered class, so these take precedence for SDK
    errors and nothing else changes. Codes are distinct from the
    status-derived ones so a client can tell "Graph refused the user's
    permissions" from "the token lacked a scope" without string-matching.
    """

    @app.exception_handler(GraphThrottledError)
    async def _throttled(request: Request, exc: GraphThrottledError) -> JSONResponse:
        """429, propagating Graph's own ``Retry-After``. The SDK's middleware
        already retried before this surfaced, so the budget is spent — a
        caller that guesses a backoff extends the throttle."""
        log.warning("Graph throttled: %s", exc)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return _envelope(429, "graph_throttled", str(exc), headers)

    @app.exception_handler(GraphAuthError)
    async def _graph_auth(request: Request, exc: GraphAuthError) -> JSONResponse:
        """403 — Graph refused the *user's* permissions: usually a delegated
        permission that was never granted or admin-consented."""
        log.error("Graph auth error: %s", exc, exc_info=exc)
        return _envelope(403, "graph_forbidden", str(exc))

    @app.exception_handler(GraphNotFoundError)
    async def _graph_not_found(request: Request, exc: GraphNotFoundError) -> JSONResponse:
        """404 — including resources Graph hides from this caller."""
        log.info("Graph not found: %s", exc)
        return _envelope(404, "graph_not_found", str(exc))

    @app.exception_handler(M365AuthError)
    async def _m365_auth(request: Request, exc: M365AuthError) -> JSONResponse:
        """502 — no Graph token could be obtained. Enriched with Entra's own
        codes: ``AADSTS65001`` means admin consent was never granted, and the
        correlation id is what the Entra sign-in logs key on."""
        detail = str(exc)
        if exc.aadsts_code:
            detail = f"{detail} [code={exc.aadsts_code}]"
        if exc.correlation_id:
            detail = f"{detail} [correlation_id={exc.correlation_id}]"
        log.error("M365 auth error: %s", detail, exc_info=exc)
        return _envelope(502, "m365_auth_failed", detail)

    @app.exception_handler(M365Error)
    async def _m365_error(request: Request, exc: M365Error) -> JSONResponse:
        """Catch-all for SDK errors, so none escapes as an untyped 500."""
        log.error("Unhandled M365 error: %s", exc, exc_info=exc)
        status = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
        return _envelope(status, "m365_error", str(exc))
