"""Per-request id + end-of-response access logging.

Vendored from Edwin's backend (``app/middleware/request_context.py``).

Two responsibilities, deliberately co-located because they share the
same scope and lifetime:

1. Stash a per-request id in a ``contextvars.ContextVar`` so any code
   running inside the request can grab it via ``get_request_id()``
   without threading the ``Request`` object through every call. The
   id is taken from the ``X-Request-ID`` request header when the
   caller supplies one (lets traces chain across services); otherwise
   we mint a fresh ``uuid4().hex``. The same id is echoed back on the
   response.
2. Emit one structured log line per request when the response
   completes — ``METHOD path -> status in dur_ms req_id=...``.
   Uvicorn's default access log fires when the response *starts*,
   which for SSE means we'd log ``200`` even if the stream errors
   half-way through. Logging in ``finally`` after the ASGI app
   returns gives us the actual end-of-response timing.

Implementation note — raw ASGI, not BaseHTTPMiddleware
------------------------------------------------------
``starlette.middleware.base.BaseHTTPMiddleware`` is the ergonomic
choice but it has a long-standing quirk where it buffers streaming
response bodies before yielding them up the stack. A long-lived
``StreamingResponse`` that yields SSE frames as they arrive would be
buffered — defeating the whole point. So this is written as plain
ASGI: we wrap ``send`` to capture the status + inject the id header,
and the body flows through untouched.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_setup import get_logger

logger = get_logger(__name__)

# Default empty string (not None) so log filters that read this var
# unconditionally see a string regardless of request scope.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# ASGI exposes header names lowercased + as bytes; keep the canonical
# string form here and lowercase at the wire.
REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("latin-1")


def get_request_id() -> str:
    """Current request's id, or empty string outside of a request scope."""
    return _request_id_var.get()


def _scope_headers(scope: Scope) -> Mapping[str, str]:
    """Decode ASGI bytes-headers into a lowercase string dict."""
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


class RequestContextMiddleware:
    """Pure-ASGI request-id + access-log middleware.

    Add this as the *outermost* middleware in the chain so the access
    log captures the final status after every other middleware has
    run, and so the id is set before any handler executes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan + websocket scopes pass through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = _scope_headers(scope)
        # Honour upstream-supplied id (cross-service tracing) or mint
        # one. ``.strip()`` because some HTTP clients tack a stray \r
        # onto header values.
        req_id = (headers.get(REQUEST_ID_HEADER.lower()) or "").strip()
        if not req_id:
            req_id = uuid.uuid4().hex

        token = _request_id_var.set(req_id)
        start = time.perf_counter()
        # If the app raises before sending ``http.response.start`` we
        # still want to log a sensible status; 500 is the default
        # FastAPI/Starlette will produce in that case.
        status_code = 500

        async def _send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Inject X-Request-ID into the outgoing headers.
                # ASGI ``headers`` is a list of (bytes, bytes) tuples.
                message_headers = list(message.get("headers", []))
                message_headers.append((_REQUEST_ID_HEADER_BYTES, req_id.encode("latin-1")))
                message = {**message, "headers": message_headers}
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        finally:
            dur_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s -> %d in %.1fms req_id=%s",
                scope.get("method", "?"),
                scope.get("path", "?"),
                status_code,
                dur_ms,
                req_id,
            )
            _request_id_var.reset(token)
