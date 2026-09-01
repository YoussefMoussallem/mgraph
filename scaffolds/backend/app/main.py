"""
Backend scaffold entrypoint.

Builds the FastAPI application the frontend scaffold talks to. All
routes mount under ``/api`` (versioned inside each router: ``/api/v1/...``)
so the SPA's dev proxy and a production reverse proxy can forward one
path prefix.

Run with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.logging_setup import get_logger, init_logging
from app.middleware import RequestContextMiddleware, register_exception_handlers
from app.routes.me import router as me_router

logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: str


def create_app() -> FastAPI:
    # Resolving settings first means a broken env fails here, at boot,
    # with a clear "Field required" error — not on the first request.
    settings = get_settings()
    init_logging(settings.logging.level)

    app = FastAPI(title=settings.app.name, version="0.1.0")

    # Calling Microsoft Graph on the caller's behalf? See app/graph.py:
    #   from app import graph
    #   app = FastAPI(title=..., version=..., lifespan=graph.lifespan)
    #   graph.register_graph_error_handlers(app)   # after register_exception_handlers

    # Middleware order matters. Starlette wraps in reverse of add order:
    # the *last* `add_middleware` call ends up *outermost*. We want the
    # request flow to be:
    #
    #   RequestContext (outermost) -> CORS -> app
    #
    # so the access log captures the post-CORS final status, and CORS
    # headers are attached to error responses (browsers can't read them
    # without CORS headers). If you add rate limiting (Edwin uses
    # SlowAPI), register it first so it sits innermost.
    # Browsers reject Access-Control-Allow-Origin: * together with
    # Access-Control-Allow-Credentials: true. Wildcard origins therefore
    # disable credentials; list concrete origins (e.g. the Vite dev URL)
    # when cookies / Authorization from the browser must work cross-origin.
    cors_origins = list(settings.cors.allow_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)  # outermost

    # Every error path — HTTPException, validation, unhandled — comes
    # back in the platform envelope {"code": ..., "detail": ...}.
    register_exception_handlers(app)

    app.include_router(me_router, prefix="/api")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    logger.info("%s starting up (%s)", settings.app.name, settings.app.environment)
    return app


app = create_app()
