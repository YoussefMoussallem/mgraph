"""HTTP middleware stack for the backend scaffold.

Two concerns, two modules:

* ``request_context``   — request-id contextvar + end-of-response
  access logging. Implemented as raw ASGI middleware so it does not
  buffer streaming (SSE) response bodies.
* ``exception_handler`` — maps every error path onto the platform
  error envelope ``{"code": ..., "detail": ...}``: a catch-all for
  unhandled ``Exception``, plus envelope-shaped handlers for
  ``HTTPException`` and request validation errors.

``main.py`` wires both in via ``register_exception_handlers`` and the
``RequestContextMiddleware`` class. Edwin additionally mounts per-IP
rate limiting (SlowAPI) innermost in this stack — add it here if your
app needs it.
"""

from .exception_handler import register_exception_handlers
from .request_context import RequestContextMiddleware, get_request_id

__all__ = [
    "RequestContextMiddleware",
    "get_request_id",
    "register_exception_handlers",
]
