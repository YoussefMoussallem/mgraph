"""Credential acquisition for Microsoft Graph: on-behalf-of and app-only.

Two flows, one factory:

**Delegated (on-behalf-of)** -- the default. The service exchanges the
caller's access token for a Graph token and acts *as that user*, so Graph
itself enforces what they may see. This is the flow the architecture
standard prefers (section 4): delegated access avoids the broad tenant
consent enterprises push back on, and it means application code is not the
only thing standing between a user and the whole tenant.

**App-only (client credentials)** -- for the three things on-behalf-of
structurally cannot do: creating and renewing change-notification
subscriptions, posting as an app rather than a user, and background work
where no user request is in flight.

Certificate authentication is **not** a third flow. It is the same
client-credentials grant with a different client assertion, so it is a
branch on settings rather than a separate code path -- hardening production
from secret to certificate becomes a config change and a deployment, not a
release.

Everything here is async. ``azure.identity.aio`` is used deliberately: the
synchronous credential classes do blocking HTTP inside the event loop,
which CONTRIBUTING section 3 forbids. The import paths differ by one
segment and the sync versions work fine in tests, so this is easy to get
wrong and worth stating.

Lifecycle matters
-----------------
Async credentials hold open HTTP sessions. Every credential this module
creates must eventually be closed -- on cache eviction and on shutdown --
or the service leaks sockets slowly and invisibly until it runs out of
file descriptors days later. :meth:`M365Credentials.close` is the single
teardown entry point; wire it into your FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from azure.identity.aio import (
    CertificateCredential,
    ClientSecretCredential,
    OnBehalfOfCredential,
)

from m365_client.config import M365Settings
from m365_client.errors import M365AuthError

if TYPE_CHECKING:
    from azure.core.credentials_async import AsyncTokenCredential

__all__ = [
    "CredentialProvider",
    "M365Credentials",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class CredentialProvider(Protocol):
    """The seam a future credential source plugs into.

    Exists so the deferred third flow -- delegated access using the
    per-user encrypted refresh tokens in Identity's ``idp_oauth_token``
    table -- can be added without touching the client layer.

    That flow is *not* implemented yet, and deliberately so: ``services/
    identity/`` is currently a README-only stub with no endpoint to read a
    token from and no decryption contract. Building against an interface
    that does not exist yet means writing untestable code that gets
    rewritten when the real contract lands. Defining the shape now is
    free; implementing it now is not.
    """

    async def credential_for_user(
        self, assertion: str, user_id: str
    ) -> AsyncTokenCredential:
        """Return a credential that acts as the given user."""
        ...

    async def credential_for_app(self) -> AsyncTokenCredential:
        """Return a credential that acts as the application itself."""
        ...

    async def close(self) -> None:
        """Release every credential and its underlying HTTP session."""
        ...


class _ManagedCredential:
    """Shields a cached credential from Kiota's per-call ``close()``.

    Kiota's ``AzureIdentityAccessTokenProvider.get_authorization_token`` does
    this after every *async* token acquisition::

        if inspect.isawaitable(result):
            result = await result
            await self._credentials.close()

    That is, it closes the credential on every single Graph request. For the
    stateless credential-per-call usage the generated SDK assumes, closing is
    harmless. For a *cached* credential it is corrosive: the transport is
    torn down while we still hold the object, and MSAL's in-memory token
    cache means the damage is invisible at first. Reads keep succeeding from
    cache, and the failure only surfaces when a refresh is finally needed --
    roughly an hour into a deployment, far from the cause.

    So ``close()`` here is deliberately a no-op: it absorbs Kiota's call.
    Real teardown goes through :meth:`aclose`, which only this package's
    lifecycle invokes. Anything else is delegated to the wrapped credential,
    so it still satisfies ``AsyncTokenCredential`` structurally.
    """

    def __init__(self, inner: AsyncTokenCredential) -> None:
        self._inner = inner

    async def get_token(self, *scopes: str, **kwargs: object) -> object:
        return await self._inner.get_token(*scopes, **kwargs)

    async def close(self) -> None:
        """No-op. Absorbs Kiota's per-request close; see the class docstring."""
        return

    async def aclose(self) -> None:
        """Actually close the wrapped credential. Only this package calls it."""
        await self._inner.close()

    def __getattr__(self, name: str) -> object:
        # Forward anything else (e.g. get_token_info on newer azure-core)
        # so future protocol additions do not silently break.
        return getattr(self._inner, name)


@dataclass
class _CacheEntry:
    """A cached credential plus the wall-clock time it stops being reusable."""

    credential: _ManagedCredential
    expires_at: float


def _decode_unverified_claims(assertion: str) -> dict:
    """Read a JWT's payload without verifying its signature.

    Not verifying is correct here, not a shortcut: by the time an assertion
    reaches this package the consuming service has already validated it
    against Entra's JWKS (that is what makes the caller authenticated at
    all). Re-verifying would mean this package owning a second JWKS client
    and cache for no security gain. We are reading the payload only to give
    a better error message than Entra would.

    Raises:
        M365AuthError: If the value is not a decodable JWT.
    """
    parts = assertion.split(".")
    if len(parts) != 3:
        raise M365AuthError(
            "assertion is not a JWT (expected three dot-separated segments). "
            "Pass the raw bearer token from the Authorization header, without "
            "the 'Bearer ' prefix."
        )
    payload = parts[1]
    # JWTs use base64url without padding; b64decode requires padding.
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        # Never include the token itself in the message -- it is a live
        # bearer credential and this message will land in logs.
        raise M365AuthError(
            f"assertion payload is not decodable JSON: {type(exc).__name__}"
        ) from None
    if not isinstance(claims, dict):
        raise M365AuthError("assertion payload is not a JSON object")
    return claims


def _require_access_token(assertion: str) -> dict:
    """Reject an ID token before it reaches Entra, with a usable message.

    This is the single most common onboarding failure for a service adopting
    delegated Graph access, and Entra's native error for it is unhelpful --
    an ``AADSTS``-coded 400 that reads like a signature or audience problem
    rather than "you sent the wrong kind of token". Someone loses an
    afternoon to that.

    ``scp`` is the discriminator. ``aud`` is not: an ID token and an access
    token for your own API both carry the app's client id as their audience.
    But only an access token carries a scope claim -- ``scp`` for delegated
    tokens, ``roles`` for app-only ones. An assertion with neither is an ID
    token; one with only ``roles`` is an app token, which on-behalf-of also
    cannot use, since OBO exchanges a *user's* delegated token.

    Returns:
        The decoded claims, so callers can reuse them without decoding twice.
    """
    claims = _decode_unverified_claims(assertion)
    if "scp" in claims:
        return claims

    if "roles" in claims:
        raise M365AuthError(
            "assertion is an app-only token (has 'roles' but no 'scp'). The "
            "on-behalf-of flow exchanges a signed-in user's delegated token; "
            "for app context use credential_for_app() instead."
        )
    raise M365AuthError(
        "assertion looks like an ID token (no 'scp' claim), which Entra will "
        "not accept as an on-behalf-of assertion. On-behalf-of requires an "
        "access token issued for this service's own API scope. Check that the "
        "calling client requests api://<client-id>/<scope> and sends its "
        "access token rather than its ID token, and that the app registration "
        "exposes that scope with the Graph delegated permissions consented."
    )


class M365Credentials:
    """Builds and caches Entra credentials for Graph access.

    One instance per process, constructed at startup and closed at shutdown.
    Satisfies :class:`CredentialProvider`.

    Caching is the whole point of this class, and the two flows need
    opposite treatment:

    * **App-only is trivially cacheable.** One credential for the process
      lifetime, and ``azure-identity`` refreshes the token internally before
      expiry. Built lazily on first use so a service that never makes an
      app-only call never pays for one.

    * **On-behalf-of is not.** ``OnBehalfOfCredential`` is constructed
      *around* a specific user assertion, so building one per request means
      a full token exchange against Entra on every single API call. Hence a
      per-user cache of credential *instances*, bounded in both size and
      age.

    The cache key is ``(user_id, short hash of the assertion)``. Both halves
    earn their place: the user id identifies the principal, and the
    assertion hash means that when the caller's token is silently refreshed
    we build a fresh credential instead of reusing one wrapped around a
    now-expired assertion. The assertion is hashed rather than stored
    because it is a live bearer credential and cache contents surface in
    heap dumps and debuggers.
    """

    def __init__(self, settings: M365Settings) -> None:
        self._settings = settings
        self._app_credential: AsyncTokenCredential | None = None
        self._user_cache: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()
        # One lock guards both the app credential and the user cache.
        # Without it, two concurrent requests for the same cold user each run
        # their own on-behalf-of exchange and one of the resulting
        # credentials is orphaned unclosed. Same double-checked-locking
        # shape as ``langfuse_client.init_client`` and the scaffold's JWKS
        # client.
        self._lock = asyncio.Lock()
        self._closed = False

    # ---- app-only -------------------------------------------------------

    async def credential_for_app(self) -> AsyncTokenCredential:
        """Return the process-wide app-only credential, building it on first use.

        Raises:
            M365AuthError: If this instance has been closed, or the
                certificate cannot be read.
        """
        self._raise_if_closed()
        if self._app_credential is not None:
            return self._app_credential

        async with self._lock:
            # Re-check under the lock: another coroutine may have built it
            # while we waited.
            if self._app_credential is None:
                self._app_credential = _ManagedCredential(
                    self._build_app_credential()
                )
            return self._app_credential

    def _build_app_credential(self) -> AsyncTokenCredential:
        """Client-credentials grant, by secret or by certificate.

        Note the asymmetry in ``azure-identity``, which is easy to get wrong:
        :class:`CertificateCredential` takes a certificate *path*, while
        :class:`OnBehalfOfCredential` takes certificate *bytes*. Same PEM
        file, two different parameter shapes.
        """
        s = self._settings
        if s.certificate_path:
            return CertificateCredential(
                tenant_id=s.tenant_id,
                client_id=s.client_id,
                certificate_path=s.certificate_path,
            )
        return ClientSecretCredential(
            tenant_id=s.tenant_id,
            client_id=s.client_id,
            client_secret=str(s.client_secret),
        )

    # ---- delegated (on-behalf-of) ---------------------------------------

    async def credential_for_user(
        self, assertion: str, user_id: str
    ) -> AsyncTokenCredential:
        """Return a credential that acts as the user who owns ``assertion``.

        Args:
            assertion: The caller's access token, raw (no ``Bearer`` prefix).
                Must be an access token for this service's own API scope --
                see :func:`_require_access_token` for why an ID token fails.
            user_id: Stable principal id, normally the Entra ``oid``. Used
                only as part of the cache key.

        Raises:
            M365AuthError: If the assertion is unusable for on-behalf-of, or
                this instance has been closed.
        """
        self._raise_if_closed()
        if not assertion:
            raise M365AuthError("assertion is required for the on-behalf-of flow")
        if not user_id:
            raise M365AuthError("user_id is required to key the credential cache")

        # Preflight before any lock or network call: a bad token should fail
        # in microseconds, not after a round-trip to Entra.
        _require_access_token(assertion)

        key = (user_id, self._assertion_fingerprint(assertion))
        now = time.monotonic()

        async with self._lock:
            entry = self._user_cache.get(key)
            if entry is not None:
                if entry.expires_at > now:
                    # Refresh LRU position on hit.
                    self._user_cache.move_to_end(key)
                    return entry.credential
                # Aged out: drop it and build fresh. Closing releases the
                # session the stale credential still holds.
                del self._user_cache[key]
                await self._safe_close(entry.credential)

            credential = _ManagedCredential(self._build_user_credential(assertion))
            self._user_cache[key] = _CacheEntry(
                credential=credential,
                expires_at=now + self._settings.cache.ttl_seconds,
            )
            self._user_cache.move_to_end(key)
            await self._evict_overflow()
            return credential

    def _build_user_credential(self, assertion: str) -> AsyncTokenCredential:
        """On-behalf-of exchange, by secret or by certificate.

        The app authenticates itself here *as well as* presenting the user's
        assertion -- an on-behalf-of exchange is a confidential-client
        operation. That is why a client credential is mandatory even for a
        service that only ever makes delegated calls.
        """
        s = self._settings
        if s.certificate_path:
            try:
                # Bytes, not a path -- unlike CertificateCredential above.
                with open(s.certificate_path, "rb") as fh:
                    cert_bytes = fh.read()
            except OSError as exc:
                raise M365AuthError(
                    f"could not read certificate_path {s.certificate_path!r}: {exc}"
                ) from None
            return OnBehalfOfCredential(
                tenant_id=s.tenant_id,
                client_id=s.client_id,
                client_certificate=cert_bytes,
                user_assertion=assertion,
            )
        return OnBehalfOfCredential(
            tenant_id=s.tenant_id,
            client_id=s.client_id,
            client_secret=str(s.client_secret),
            user_assertion=assertion,
        )

    @staticmethod
    def _assertion_fingerprint(assertion: str) -> str:
        """Short, non-reversible stand-in for the assertion in the cache key.

        Truncated to 16 hex chars: long enough that a collision between two
        live tokens is not a practical concern, short enough to keep keys
        cheap. The raw token never enters the cache.
        """
        return hashlib.sha256(assertion.encode("utf-8")).hexdigest()[:16]

    async def _evict_overflow(self) -> None:
        """Trim the cache to ``max_entries``, closing what we drop.

        Caller must hold ``self._lock``.
        """
        max_entries = self._settings.cache.max_entries
        while len(self._user_cache) > max_entries:
            _, entry = self._user_cache.popitem(last=False)  # least recently used
            await self._safe_close(entry.credential)

    # ---- lifecycle ------------------------------------------------------

    async def close(self) -> None:
        """Close every credential this instance created.

        Idempotent, and safe to call on a never-used instance. Wire into
        your FastAPI lifespan shutdown -- skipping it produces "Unclosed
        client session" warnings on every restart and leaks sockets under
        load.

        After this, the credential accessors raise :class:`M365AuthError`
        rather than handing back credentials whose transport is gone.
        """
        async with self._lock:
            self._closed = True
            for entry in self._user_cache.values():
                await self._safe_close(entry.credential)
            self._user_cache.clear()
            if self._app_credential is not None:
                await self._safe_close(self._app_credential)
                self._app_credential = None

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise M365AuthError(
                "M365Credentials is closed; build a new instance rather than "
                "reusing one past shutdown"
            )

    @staticmethod
    async def _safe_close(credential: _ManagedCredential) -> None:
        """Really close a credential, logging rather than raising on failure.

        Goes through ``aclose`` rather than ``close`` -- see
        :class:`_ManagedCredential` for why ``close`` is a deliberate no-op.

        Teardown must not mask the real error that triggered it, and a
        failure to close is not something the caller can act on. Same
        never-throw stance as ``langfuse_client.shutdown``.
        """
        try:
            await credential.aclose()
        except Exception:
            logger.warning("failed to close credential", exc_info=True)

    # ---- introspection (tests / diagnostics) ----------------------------

    @property
    def cached_user_count(self) -> int:
        """How many user credentials are currently cached."""
        return len(self._user_cache)
