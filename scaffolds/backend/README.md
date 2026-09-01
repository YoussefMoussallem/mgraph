# Backend scaffold

Starter template for AI Labs backends. FastAPI service with Microsoft Entra ID (Azure AD) authentication wired end to end: JWT validation against Microsoft's JWKS, the platform error envelope, request-id tracing, and a test suite that exercises real RS256 tokens. Apps that call Microsoft Graph on the user's behalf get that wiring too — `app/graph.py`, optional, on the [`m365-client`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks/python/m365) SDK family.

Extracted from Edwin's backend (`edwin/server/backend`), trimmed to the generic app skeleton. It is the server-side counterpart of [`scaffolds/frontend/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/scaffolds/frontend) — the SPA acquires an Entra ID token via MSAL and this backend validates it; run the two together and the frontend's home screen calls `/api/v1/me` here.

## Starting a new app from this scaffold

1. Copy `scaffolds/backend/` into your project (it is a template, not a dependency — you own the copy).
2. Create a venv and install: `uv venv && uv pip install -e ".[test]"` (or `pip install -e ".[test]"`). Add the `m365` extra (`".[test,m365]"`) only if the app will call Microsoft Graph.
3. `cp .env.example .env` and fill in `AZURE_CLIENT_ID` + `AZURE_TENANT_ID` (same values as the frontend's `VITE_AZURE_*`). Leave them blank to run without Azure — `get_current_user` then returns an anonymous dev user.
4. `uvicorn app.main:app --reload` — then `GET /health`, and `GET /api/v1/me` with a bearer token from the SPA.
5. Add your app's routers under `app/routes/` and mount them in `create_app()`.

Run tests with `pytest`.

## How authentication works

MSAL on the frontend sends an Entra token as `Authorization: Bearer <jwt>` — the **ID token** by default, or an **access token** for this API's own scope when the app calls Microsoft Graph (see below). `app/dependencies.py` validates it and hands your route a `CurrentUser`:

1. Parse the `Bearer` header (scheme case-insensitive per RFC 6750).
2. Fetch the signing key for the token's `kid` from Microsoft's JWKS (`PyJWKClient`, cached per process; corporate CA bundles via `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`).
3. Verify RS256 signature, `aud` (the app registration's client id, bare or as `api://<client-id>`), `iss` (the tenant's v2.0 issuer or its v1 `sts.windows.net` form — portal-created registrations default to v1), `exp` (60s leeway), and required claims.
4. Map claims → `CurrentUser`: `user_id` from `oid` (fallback `sub`), best-effort `email` (`preferred_username` → `email` → `upn`), `display_name`.
5. With `API_REQUIRED_SCOPE` set, require the `scp` claim (401 with a message naming the fix when an ID token was sent instead) and that it carries the scope (403 otherwise); the raw token is kept as `CurrentUser.assertion` for the on-behalf-of exchange.

Failures return 401 with a generic message (the real reason is logged, not leaked); an expired token gets the distinct detail `"Token has expired"` so the frontend can silently refresh rather than force a re-login.

### Dev fallback

When `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` are unset, `get_current_user` returns an anonymous dev user so the backend runs without an Azure setup. Production refuses to boot with them unset (`config._validate_production`), so the fallback can never ship.

Tokens are audience-bound to the app registration. If your app needs a second registration (Edwin, for instance, adds a separate admin app), add a sibling dependency that calls `_validate_token` with that client id — the scaffold deliberately ships only the generic single-audience setup.

### Usage

```python
from fastapi import APIRouter, Depends
from app.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/v1", tags=["projects"])

@router.get("/projects", response_model=ProjectList)
async def list_projects(user: CurrentUser = Depends(get_current_user)) -> ProjectList:
    return await project_service.list_for(user.user_id)
```

### Identity service (SS-01) extension point

Today this validates Entra tokens directly. When the [SS-01 identity service](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/identity) ships its Entra-token → platform-JWT exchange, `app/dependencies.py` is the single place to change: validate the platform JWT (issuer/keys from SS-01) instead of Microsoft's. The `CurrentUser` contract for routes stays the same. The frontend scaffold has the mirror-image note in its `useToken.js`.

## Calling Microsoft Graph on behalf of the user

Optional. Everything an app needs to read Outlook or SharePoint *as the signed-in user* is in `app/graph.py`, built on the [`m365-client`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks/python/m365) SDK family. Delete that module and the `m365` extra if your app does not call Graph; nothing else depends on them.

1. `pip install -e ".[test,m365]"` — pulls `m365-client`. Add `outlook-client` / `sharepoint-client` for the typed workload calls.
2. In the app registration: **expose an API scope** (`api://<client-id>/access_as_user`), add the Graph **delegated** permissions the app needs (`User.Read`, then per workload only what it calls — `Mail.Read` or `Mail.Send`, `Sites.Read.All` or `Files.ReadWrite.All`, …; each SDK README lists them by operation) and **grant admin consent**, and add a **client credential** — the on-behalf-of exchange is a confidential-client operation.
3. In `.env`: `API_REQUIRED_SCOPE=access_as_user` and `AZURE_CLIENT_SECRET` (or `AZURE_CERTIFICATE_PATH`).
4. In `app/main.py`: `FastAPI(..., lifespan=graph.lifespan)` and `graph.register_graph_error_handlers(app)` after `register_exception_handlers(app)`.
5. In a route:

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from msgraph import GraphServiceClient
from outlook_client import OutlookClient

from app.graph import get_graph

router = APIRouter(prefix="/v1", tags=["mail"])

@router.get("/messages")
async def messages(client: Annotated[GraphServiceClient, Depends(get_graph)]):
    return await OutlookClient(client).list_messages(top=10)
```

Setting `API_REQUIRED_SCOPE` switches `get_current_user` from validating the ID token to requiring an **access token for this API's own scope** — the only kind Entra will exchange on-behalf-of. The frontend has to request that scope (`api://<client-id>/access_as_user`) and send `accessToken`, not `idToken`; the frontend scaffold's `useToken.js` returns the ID token and needs exactly that change. An ID token sent by mistake gets a 401 whose message names the fix, rather than an opaque failure from deep inside the exchange.

SDK errors propagate from routes to the handlers `register_graph_error_handlers` installs: `429` `graph_throttled` with Graph's `Retry-After`, `403` `graph_forbidden` (a delegated permission not granted or consented), `404` `graph_not_found`, and `502` `m365_auth_failed` carrying the `AADSTS` code when the exchange itself fails — `AADSTS65001` means admin consent was never granted.

Why delegated: Graph returns only what the caller can already see, so application code is never the sole gate on tenant data, and no tenant-wide `Sites.Read.All` application permission (or `Sites.Selected` provisioning) is needed. App-only access (`M365Client.graph_for_app()`) exists for background jobs with no user present; reaching for it is a deliberate, reviewable decision.

## App shell

- **Config** (`app/config.py`) — two-layer pydantic-settings: flat `_EnvSettings` (every key required, no defaults — missing keys crash at boot) reshaped into frozen groups (`settings.azure_ad`, `settings.cors`, ...). `APP_ENVIRONMENT=production` never loads `.env` files and refuses to boot without the Azure AD registration.
- **Request context** (`app/middleware/request_context.py`) — raw-ASGI middleware (doesn't buffer SSE): per-request id in a `ContextVar` (honours inbound `X-Request-ID`, echoes it back) plus one access-log line at end-of-response.
- **Error envelope** (`app/middleware/exception_handler.py`) — every failure path returns `{"code": "...", "detail": "..."}` per the platform API standard: `HTTPException`s get a status-derived code, validation errors keep FastAPI's field-level list, and unhandled exceptions become a stable JSON 500 with the traceback logged under the request id.
- **Microsoft Graph** (`app/graph.py`, optional) — one `M365Client` per process via `lifespan`, `get_graph` for a Graph client acting as the caller, and envelope handlers for the SDK's typed errors. Deletable if the app does not call Graph.
- **Entrypoint** (`app/main.py`) — `create_app()` wires middleware in the documented order (RequestContext outermost → CORS → app), registers the envelope handlers, mounts routers under `/api` (paths versioned `/v1/...`), and exposes `/health`.

## Differences from the source (Edwin)

- **Auth dependencies are sync `def`, not `async def`.** `PyJWKClient` fetches the JWKS with blocking urllib I/O; inside an `async def` dependency that (rarely, on key rotation) stalls the event loop. FastAPI runs sync dependencies in its threadpool, so the blocking fetch is contained. This aligns with the repo's no-blocking-I/O-in-async rule.
- **Errors use the `{code, detail}` envelope** everywhere (Edwin returns FastAPI's bare `{"detail": ...}`), matching CONTRIBUTING's API design rules and what the frontend scaffold's `api/client.js` parses.
- **Routes are versioned `/api/v1/...`** from day one (Edwin mounts unversioned `/api/...`).
- **Stdlib logging** replaces the shared `app-logger` package (same `get_logger`/`init_logging` call shape, so swapping a richer sink in later touches one file).
- **No second (admin) audience.** Edwin ships a separate admin app registration with its own `get_admin_user` dependency — that's Edwin-specific, not a platform pattern. The scaffold stays single-audience; the README section above notes how to add one if your app needs it.
- Edwin-specific subsystems are stripped: agent/MCP lifespan, db-service proxy, LLM/Langfuse bridges, rate limiting (SlowAPI — the middleware docstrings note where it slots back in), document pipelines.

## Layout

```
scaffolds/backend/
├── pyproject.toml        # deps + pytest config (hatchling build)
├── .env.example          # every required key, annotated
├── app/
│   ├── main.py           # create_app(): middleware order, envelope handlers, routers, /health
│   ├── config.py         # two-layer settings singleton, production validation
│   ├── dependencies.py   # Entra ID JWT validation → CurrentUser (ID token, or access token + scp)
│   ├── graph.py          # optional: Microsoft Graph as the caller (m365-client wiring)
│   ├── logging_setup.py  # stdlib get_logger/init_logging (app-logger call shape)
│   ├── middleware/       # request-id/access-log ASGI middleware + error envelope
│   └── routes/me.py      # /v1/me — replace with your resources
└── tests/
    ├── conftest.py       # env bootstrap, RSA keypair, token minting, JWKS stub
    ├── test_auth.py      # token validation unit tests (real RS256 sign/verify)
    ├── test_graph.py     # optional Graph wiring: scope mode, get_graph, error mapping
    └── test_api.py       # full-app tests: routes, envelope, request-id echo
```

## Dependencies and why

| Package | Why |
| --- | --- |
| `fastapi` + `uvicorn[standard]` | Web framework + ASGI server. |
| `pydantic` + `pydantic-settings` | Request/response models; required-key env config with `.env` support in dev. |
| `PyJWT[crypto]` | Entra ID token validation — JWKS fetching (`PyJWKClient`) and RS256 verification. |
| `certifi` | Default CA bundle for the JWKS TLS connection (overridable for corporate proxies). |
| `m365-client` (`m365` extra) | Microsoft Graph on the caller's behalf — on-behalf-of exchange, credential cache, typed errors. Only with `app/graph.py`. |
| `pytest`, `pytest-asyncio`, `httpx` (test) | Test runner; `httpx` powers FastAPI's `TestClient`. |
