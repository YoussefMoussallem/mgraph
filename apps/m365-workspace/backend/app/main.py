"""M365 Workspace backend entrypoint.

Started from the backend scaffold and wired for Microsoft Graph from day
one: ``app/graph.py`` builds the process-wide ``M365Client`` in the lifespan
and turns every authenticated caller into a Graph client acting as that
user. Three routers sit on top of the SDK family:

* ``/api/v1/outlook/...``     — ``outlook-client``: mail, attachments, calendar, contacts
* ``/api/v1/sharepoint/...``  — ``sharepoint-client``: sites, libraries, files, lists
* ``/api/v1/agent/...``       — ``m365-langchain-tools``: the same workloads as LLM tools

Run with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import graph
from app.config import get_settings
from app.logging_setup import get_logger, init_logging
from app.middleware import RequestContextMiddleware, register_exception_handlers
from app.routes.agent import router as agent_router
from app.routes.me import router as me_router
from app.routes.outlook import router as outlook_router
from app.routes.sharepoint import router as sharepoint_router

logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: str


def register_sdk_argument_handler(app: FastAPI) -> None:
    """Render the SDKs' argument checks as 400s.

    ``outlook-client`` and ``sharepoint-client`` raise ``ValueError`` before
    any network call when an argument cannot work — a malformed address, a
    name containing ``/``, an attachment over Graph's size limit. Those are
    caller errors, so they belong in the envelope as ``bad_request`` with
    the SDK's own message, not in the catch-all 500. Registered after the
    Graph handlers; Starlette picks the most specific class, so the SDK's
    typed errors keep their own mappings.
    """

    @app.exception_handler(ValueError)
    async def _bad_argument(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"code": "bad_request", "detail": str(exc)})


def create_app() -> FastAPI:
    # Resolving settings first means a broken env fails here, at boot,
    # with a clear "Field required" error — not on the first request.
    settings = get_settings()
    init_logging(settings.logging.level)

    app = FastAPI(title=settings.app.name, version="0.1.0", lifespan=graph.lifespan)

    # Middleware order matters: RequestContext (outermost) -> CORS -> app,
    # so the access log sees the final status and CORS headers reach
    # error responses. See the scaffold for the full rationale.
    cors_origins = list(settings.cors.allow_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        # The SPA reads the download file name off attachment/file responses.
        expose_headers=["Content-Disposition", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)  # outermost

    # Every error path — HTTPException, validation, unhandled — comes back
    # in the platform envelope {"code": ..., "detail": ...}; the SDK's typed
    # errors and argument checks get their own codes on top.
    register_exception_handlers(app)
    graph.register_graph_error_handlers(app)
    register_sdk_argument_handler(app)

    app.include_router(me_router, prefix="/api")
    app.include_router(outlook_router, prefix="/api")
    app.include_router(sharepoint_router, prefix="/api")
    app.include_router(agent_router, prefix="/api")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    logger.info("%s starting up (%s)", settings.app.name, settings.app.environment)
    return app


app = create_app()
