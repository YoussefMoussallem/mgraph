"""FastAPI dependencies — Microsoft Entra ID token validation.

Vendored from Edwin's backend (``app/dependencies.py``) with two deliberate
changes:

* The dependencies are plain ``def`` instead of ``async def``.
  ``PyJWKClient`` fetches Microsoft's JWKS with blocking urllib I/O (rarely
  — keys are cached), and a blocking call inside an ``async def``
  dependency would stall the event loop. FastAPI runs sync dependencies in
  its threadpool, so the occasional JWKS refresh never blocks other
  requests.
* Two token shapes are accepted, selected by ``API_REQUIRED_SCOPE``. See
  the next section.

Which token: ID token or access token
-------------------------------------
An app that only needs to know *who is calling* validates the Entra **ID
token** MSAL hands the SPA at sign-in. That is the default: leave
``API_REQUIRED_SCOPE`` blank.

An app that calls **Microsoft Graph on the caller's behalf** (see
``app/graph.py``) needs something an ID token cannot provide: an **access
token issued for this API's own scope** (``api://<client-id>/access_as_user``),
because that is the only thing Entra accepts as an on-behalf-of assertion.
Set ``API_REQUIRED_SCOPE`` to the scope name and the dependency additionally
requires the ``scp`` claim to carry it — and rejects an ID token with a
message that says exactly what to send instead, because Entra's own error
for that mistake reads like a signature problem. ``scp`` is the
discriminator, not ``aud``: an ID token and an access token for the same
app carry the same audience.

Both Entra issuer formats are accepted — ``login.microsoftonline.com/<tid>/v2.0``
and the v1 ``sts.windows.net/<tid>/`` — because ``accessTokenAcceptedVersion``
defaults to ``null`` (v1) for registrations created in the portal, and a
v1-only tenant would otherwise see every call fail with a generic "Invalid
token". Likewise ``aud`` may be the bare client id or the ``api://<client-id>``
URI, depending on how the caller requested the token. Either way the token
is still pinned to this tenant and this registration.

How to use
----------
Inject into any FastAPI handler that needs an authenticated caller::

    from fastapi import Depends
    from app.dependencies import get_current_user, CurrentUser

    @router.get("/me")
    async def me(user: CurrentUser = Depends(get_current_user)):
        return {"id": user.user_id, "email": user.email}

Dev fallback
------------
In dev, when ``AZURE_CLIENT_ID`` / ``AZURE_TENANT_ID`` are unset,
``get_current_user`` returns an "anonymous" user so the backend is
usable without an Azure setup. ``config._validate_production`` refuses
to boot production with them unset, so the fallback can never ship.

Audience note: tokens are ``aud``-bound to one app registration. If
your app grows a second registration (Edwin has a separate admin app),
add a sibling dependency that calls ``_validate_token`` with that
client id — do not widen this one.
"""

from __future__ import annotations

import os
import ssl
import threading
from dataclasses import dataclass

import certifi
import jwt
from fastapi import Header, HTTPException

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    """Identity passed around by request handlers.

    Fields:
        user_id:      Stable per-user identifier - Azure ``oid`` when
                      available, otherwise ``sub``. Use this as the FK
                      into our own users table.
        email:        Best-effort email - claims vary by tenant config,
                      so we try ``preferred_username`` -> ``email`` ->
                      ``upn`` and fall back to "".
        display_name: Human-readable name (``name`` claim) or email.
        azure_oid:    Raw Azure object ID. Same as ``user_id`` today,
                      kept as a distinct field so call sites that
                      specifically need the Azure OID don't break if we
                      ever change how ``user_id`` is derived.
        assertion:    The raw access token, kept only in access-token
                      mode (``API_REQUIRED_SCOPE`` set) for the
                      on-behalf-of exchange in ``app/graph.py``. It is a
                      live bearer credential — never log it.
        scopes:       Values of the ``scp`` claim; empty for an ID token.
    """

    user_id: str
    email: str
    display_name: str
    azure_oid: str | None = None
    assertion: str | None = None
    scopes: tuple[str, ...] = ()


# ── JWKS cache ────────────────────────────────────────────────────────
# Microsoft rotates signing keys; the PyJWKClient fetches + caches them
# so we don't hit Microsoft on every request. One shared client per
# process is enough - the tenant_id is fixed for the app's lifetime, so
# we don't key the cache by tenant.

_jwks_client: jwt.PyJWKClient | None = None
_jwks_lock = threading.Lock()


def _jwks_ssl_context() -> ssl.SSLContext:
    """Build the SSL context used for fetching Microsoft's JWKS.

    CA bundle resolution (first match wins). **Do not** commit PEM files
    into the repo — set one of these env vars to a path on disk instead:

      1. ``SSL_CERT_FILE`` — common Python / OpenSSL convention.
      2. ``REQUESTS_CA_BUNDLE`` — used by many HTTP stacks; same effect here.
      3. ``certifi.where()`` — default public CA bundle.
    """
    cafile = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE") or certifi.where()
    return ssl.create_default_context(cafile=cafile)


def _get_jwks_client() -> jwt.PyJWKClient:
    """Lazily build the process-wide JWKS client (double-checked locking).

    The client is keyed only by ``tenant_id`` from settings, which is
    fixed for the lifetime of the process, so one client serves every
    audience in the tenant.
    """
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                tenant_id = get_settings().azure_ad.tenant_id
                jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
                _jwks_client = jwt.PyJWKClient(
                    jwks_url,
                    cache_keys=True,
                    ssl_context=_jwks_ssl_context(),
                )
    return _jwks_client


# ── Shared token validation ──────────────────────────────────────────


def _validate_token(
    authorization: str | None,
    client_id: str,
    tenant_id: str,
    required_scope: str = "",
) -> CurrentUser:
    """Parse + verify a bearer token against Azure AD.

    Validation steps (all must pass, order matters):
      1. Header must be ``Bearer <token>`` - otherwise 401.
      2. Pick the right signing key from Microsoft's JWKS by the token's
         ``kid`` header.
      3. Verify RS256 signature + standard claims:
           - ``aud`` must be ``client_id`` or ``api://<client_id>``
             (prevents tokens issued for a different app registration
             from being accepted here).
           - ``iss`` must be our tenant's v2.0 issuer URL or its v1
             ``sts.windows.net`` form.
           - ``exp``, ``iss``, ``aud``, ``sub`` must all be present.
      4. If any of the above fail, raise 401 with a generic message -
         we log the real reason but don't leak it to the client.
      5. When ``required_scope`` is set, the ``scp`` claim must exist
         (401 otherwise: an ID token was sent) and carry the scope (403
         otherwise). These two are *not* generic: the remedy is specific
         and telling the caller saves an afternoon.

    Returns a ``CurrentUser`` built from the token's claims. Never
    trust header values beyond the token itself - the claims below
    come from a signature-verified payload.
    """
    # RFC 6750 says the scheme is case-insensitive ("Bearer", "bearer",
    # "BEARER" all valid). ``partition`` also tolerates extra whitespace
    # in the token portion without us having to maintain a magic offset.
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=[client_id, f"api://{client_id}"],
            issuer=[
                f"https://login.microsoftonline.com/{tenant_id}/v2.0",
                f"https://sts.windows.net/{tenant_id}/",
            ],
            options={"require": ["exp", "iss", "aud", "sub"]},
            # 60s leeway absorbs small clock skew between the caller's
            # machine and Azure. Microsoft's own SDKs use the same value.
            leeway=60,
        )
    except jwt.ExpiredSignatureError:
        # Split out so the frontend can distinguish "token expired,
        # silently refresh" from "token fundamentally bad, force re-login".
        # ``from None`` suppresses the PyJWT chain in any logged traceback.
        raise HTTPException(status_code=401, detail="Token has expired") from None
    except jwt.InvalidTokenError as exc:
        # Log the underlying reason (kid mismatch, bad aud, etc.) but
        # return a generic 401 - attackers don't need our diagnostics.
        log.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from None

    scopes = tuple(str(claims.get("scp", "")).split())
    if required_scope:
        if not scopes:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Token has no 'scp' claim, so it is an ID token rather than an "
                    f"access token. Request a token for api://{client_id}/{required_scope} "
                    "and send that instead."
                ),
            )
        if required_scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Token is missing the required scope '{required_scope}'",
            )

    # ``oid`` is the stable Azure object ID; fall back to ``sub`` for
    # tokens where ``oid`` isn't issued (personal MS accounts, some
    # guest scenarios).
    oid = claims.get("oid") or claims.get("sub")
    # Email claim naming is inconsistent across tenants / account types,
    # so we try several in order of preference.
    email = claims.get("preferred_username") or claims.get("email") or claims.get("upn") or ""
    display_name = claims.get("name") or email

    return CurrentUser(
        user_id=oid,
        email=email,
        display_name=display_name,
        azure_oid=oid,
        # Only an access token is worth keeping: it is what the
        # on-behalf-of exchange needs, and an ID token cannot be exchanged.
        assertion=token if required_scope else None,
        scopes=scopes,
    )


# ── Dependency ───────────────────────────────────────────────────────
# Plain ``def`` on purpose — see module docstring. FastAPI threadpools
# sync dependencies, so the JWKS fetch can block without stalling the
# event loop.


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """FastAPI dependency: validate the caller's Entra token.

    Validates the ID token by default; with ``API_REQUIRED_SCOPE`` set,
    an access token carrying that scope (see the module docstring).

    Dev fallback: if ``azure_ad.client_id`` or ``azure_ad.tenant_id`` is
    unset, skip validation entirely and return a hard-coded anonymous
    user. This lets new contributors run the backend without an Azure
    setup. Do NOT deploy with these unset — ``config._validate_production``
    refuses to boot production without them.
    """
    settings = get_settings()
    client_id = settings.azure_ad.client_id
    tenant_id = settings.azure_ad.tenant_id

    if not client_id or not tenant_id:
        return CurrentUser(
            user_id="anonymous",
            email="anonymous@dev.local",
            display_name="Anonymous (dev)",
        )

    return _validate_token(authorization, client_id, tenant_id, settings.azure_ad.required_scope)
