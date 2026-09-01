"""Error responses — one consistent envelope for every failure path.

Adapted from Edwin's catch-all handler, extended to the platform API
standard: errors on the wire always look like::

    { "code": "unauthorized", "detail": "Invalid token" }

Three handlers are registered:

* ``HTTPException``          — FastAPI's default returns only
  ``{"detail": ...}``; ours adds a machine-readable ``code`` derived
  from the status so clients can branch without string-matching.
* ``RequestValidationError`` — 422 with the envelope plus the
  field-level ``errors`` list frontends use to highlight bad inputs.
* bare ``Exception``         — unhandled errors get a stable JSON 500
  (no stack trace on the wire) and a full traceback in the logs, tied
  to the request id from ``RequestContextMiddleware``.

Streaming endpoints (SSE) catch their own errors inside the generator
and yield an in-band ``error`` event — the response has already
started by then, so no handler here can intercept. That's the correct
behaviour: status 200 + a structured error event, instead of a torn 500.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_setup import get_logger

from .request_context import get_request_id

logger = get_logger(__name__)

# Machine-readable codes for the statuses this scaffold raises. Anything
# else falls back to "http_<status>" — still stable, still branchable.
_CODE_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _code_for(status: int) -> str:
    return _CODE_BY_STATUS.get(status, f"http_{status}")


async def _http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Wrap HTTPException in the ``{code, detail}`` envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": _code_for(exc.status_code), "detail": str(exc.detail)},
        headers=getattr(exc, "headers", None),
    )


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """422 in the envelope, keeping FastAPI's field-level error list."""
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "detail": "Request validation failed",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


async def _unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Log the trace with request context, return a stable JSON 500."""
    req_id = get_request_id()
    logger.exception(
        "Unhandled exception on %s %s (req_id=%s): %s",
        request.method,
        request.url.path,
        req_id,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "detail": "Internal server error",
            "request_id": req_id or None,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the envelope handlers onto the app."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
