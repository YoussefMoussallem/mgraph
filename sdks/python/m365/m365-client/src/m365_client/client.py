"""Construction of configured, hardened ``GraphServiceClient`` instances.

This is the package's public front door. It hands back the *official*
``GraphServiceClient`` rather than a wrapper, which is the deliberate
consequence of this SDK shipping no workload helpers: consuming services
own their Graph calls and need the full typed Graph surface, so wrapping it
would only take capability away.

What that buys and costs
------------------------
Anything configurable at construction time is free for consumers -- they
get it without knowing it exists:

* **Retry and throttling.** Graph's own middleware honours ``Retry-After``
  on 429 and 503. This is the single strongest argument for the official
  SDK: Graph throttles aggressively and per-workload, and a hand-rolled
  retry loop that ignores ``Retry-After`` gets throttled harder.
* **Redirect handling, telemetry headers, compression.**
* **Timeouts.** Graph can hang; an open-ended call ties up a worker
  indefinitely (CONTRIBUTING section 4).

Anything needing call *interception* cannot be automatic, because this
package is not in the call path once it returns the client. That is why
error translation is an explicit context manager
(:func:`~m365_client.errors.translate_graph_errors`) rather than
middleware. It is the documented calling convention.

Two silent gotchas this module exists to absorb
-----------------------------------------------
Both are triggered by supplying our own httpx client, which we must do to
set timeouts, and both fail quietly rather than loudly.

1. **The ``/me`` rewrite is middleware, and it is conditional.**
   ``GraphRequestAdapter`` installs a ``UrlReplaceHandlerOption`` mapping
   ``/users/me-token-to-replace`` -> ``/me`` *only when it builds its own
   client*. ``GraphServiceClient.me`` is generated as
   ``users.by_user_id("me-token-to-replace")`` and depends on that rewrite,
   so passing our own client without those options sends every ``client.me
   .*`` call to a literal ``/users/me-token-to-replace`` URL. We therefore
   reuse msgraph's own options dict rather than reimplementing it, so the
   rewrite (and its telemetry sibling) stay correct across SDK upgrades.

2. **``base_url`` gains a trailing slash, and Kiota concatenates.**
   httpx normalises ``base_url`` to end in ``/``; Kiota reads it back as the
   ``baseurl`` path parameter and expands ``{+baseurl}/users/...``, yielding
   ``https://graph.microsoft.com/v1.0//users/...``. We set the adapter's
   ``base_url`` explicitly, without the slash, so URLs come out clean.

``GraphClientFactory.create_with_default_middleware`` also ignores its
``api_version`` and ``host`` arguments entirely when given a client, which
is why the base URL is composed here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from kiota_http.middleware.options import RetryHandlerOption
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter
from msgraph_core import APIVersion, GraphClientFactory, NationalClouds

from m365_client.config import M365Settings
from m365_client.credentials import M365Credentials

if TYPE_CHECKING:
    from azure.core.credentials_async import AsyncTokenCredential

__all__ = ["M365Client"]

logger = logging.getLogger(__name__)

_API_VERSIONS = {"v1.0": APIVersion.v1, "beta": APIVersion.beta}


def _msgraph_default_options() -> dict:
    """msgraph's own middleware options: the ``/me`` rewrite plus telemetry.

    Borrowed rather than reimplemented on purpose. The rewrite's replacement
    map is an internal detail of the generated SDK, and hardcoding a copy
    here would silently rot the day Microsoft changes it. Reading theirs
    means an SDK upgrade carries its own correctness with it.

    Degrades to an empty dict if a future version moves the attribute:
    losing telemetry headers is cosmetic, and the ``/me`` breakage that
    would follow surfaces as a 404 on the first ``client.me`` call, with the
    warning below in the log next to it.

    Note their ``GraphTelemetryHandlerOption`` pins ``api_version`` to v1
    regardless of the version actually in use. That only affects a
    telemetry header, and it is msgraph's own behaviour for every consumer
    that lets it build its client, so it is left alone.
    """
    try:
        from msgraph.graph_request_adapter import options

        return dict(options)
    except (ImportError, AttributeError):  # pragma: no cover - defensive
        logger.warning(
            "could not read msgraph default middleware options; the /me URL "
            "rewrite may be inactive"
        )
        return {}


class M365Client:
    """Hands out authenticated Graph clients, one per calling identity.

    Construct once at startup, share across requests, and close at
    shutdown::

        m365 = M365Client(settings)
        ...
        await m365.close()

    A ``GraphServiceClient`` is cheap to build once its credential exists
    (the expensive part -- the token exchange -- is cached inside
    :class:`~m365_client.credentials.M365Credentials`), so clients are built
    per call rather than cached. That keeps this class stateless apart from
    the credential cache and avoids a second eviction policy.
    """

    def __init__(
        self,
        settings: M365Settings,
        *,
        credentials: M365Credentials | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            settings: Validated configuration.
            credentials: Override the credential factory. Tests inject a fake
                here to avoid touching Entra.
            transport: Swap the bottom of the HTTP stack while keeping
                everything above it. The Graph middleware, base URL, and
                timeouts still apply, because the middleware wraps whatever
                transport the client was built with.

                This is the seam for two jobs. Tests pass
                ``httpx.MockTransport`` to serve canned Graph responses with
                no network, and still exercise the real configuration.
                Deployments behind a corporate proxy or private CA pass
                ``httpx.AsyncHTTPTransport(verify=..., proxy=...)``.

                Injecting a whole ``httpx.AsyncClient`` is deliberately not
                offered: it would bypass the Graph middleware, and losing the
                ``/me`` URL rewrite that way fails silently at runtime rather
                than loudly at construction.
        """
        self._settings = settings
        self._credentials = credentials or M365Credentials(settings)
        self._transport_override = transport
        self._http: httpx.AsyncClient | None = None

    # ---- public API -----------------------------------------------------

    async def graph_for_user(
        self, assertion: str, user_id: str
    ) -> GraphServiceClient:
        """A Graph client acting as the signed-in user (on-behalf-of).

        This is the default for anything with a user in the request path.
        Graph enforces that user's own permissions, so application code is
        not the only gate.

        Args:
            assertion: The caller's raw access token -- no ``Bearer`` prefix.
                Must be an access token for this service's own API scope; an
                ID token is rejected with an actionable message before any
                call to Entra.
            user_id: Stable principal id, normally the Entra ``oid``.

        Raises:
            M365AuthError: The assertion is unusable, or the token exchange
                failed at Entra.
        """
        credential = await self._credentials.credential_for_user(assertion, user_id)
        return self._build_client(credential)

    async def graph_for_app(self) -> GraphServiceClient:
        """A Graph client acting as the application itself (app-only).

        Use only where no user exists: change-notification subscription
        create/renew, posting as an app, and background jobs. Prefer
        :meth:`graph_for_user` whenever a user is present -- app-only
        permissions are tenant-wide, which makes your code the only
        boundary between a caller and everything in the tenant.

        Raises:
            M365AuthError: The client-credentials grant failed.
        """
        credential = await self._credentials.credential_for_app()
        return self._build_client(credential)

    async def close(self) -> None:
        """Release credentials and the shared HTTP client. Idempotent.

        Wire into your FastAPI lifespan shutdown. Closing the client also
        closes the transport it was built with, including an injected one --
        this object owns the client either way.
        """
        await self._credentials.close()
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                logger.warning("failed to close Graph HTTP client", exc_info=True)
        self._http = None

    # ---- construction ---------------------------------------------------

    def _build_client(self, credential: AsyncTokenCredential) -> GraphServiceClient:
        """Wrap a credential in a fully configured ``GraphServiceClient``.

        The chain is explicit rather than using ``GraphServiceClient
        (credentials=...)`` because that convenience constructor builds its
        own transport, which would discard our timeout and retry
        configuration.
        """
        auth_provider = AzureIdentityAuthenticationProvider(
            credentials=credential,
            scopes=list(self._settings.scopes),
        )
        adapter = GraphRequestAdapter(
            auth_provider=auth_provider,
            client=self._transport(),
        )
        # Explicit and un-slashed. Left to default, the adapter reads it back
        # from httpx -- which appends a trailing slash -- and Kiota then
        # expands "{+baseurl}/users/..." into a double slash. See gotcha 2 in
        # the module docstring.
        adapter.base_url = self.base_url
        return GraphServiceClient(request_adapter=adapter)

    def _transport(self) -> httpx.AsyncClient:
        """The shared httpx client, built once and reused.

        Shared across identities on purpose: connection pooling is per
        client, and a client per user would defeat it. Authorization is
        applied per request by the auth provider, not baked into the
        transport, so sharing is safe.
        """
        if self._http is None:
            self._http = self._build_transport()
        return self._http

    @property
    def base_url(self) -> str:
        """Canonical Graph base URL for the configured version, no trailing slash."""
        api_version = _API_VERSIONS[self._settings.api_version]
        return f"{NationalClouds.Global.value}/{api_version.value}"

    def _build_transport(self) -> httpx.AsyncClient:
        """Build the Graph-middleware-wrapped httpx client.

        The options dict carries msgraph's own middleware -- crucially the
        ``/me`` URL rewrite -- plus our retry ceiling. Supplying a client
        without those options is the failure described as gotcha 1 in the
        module docstring.
        """
        s = self._settings

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(s.timeout_seconds),
            base_url=self.base_url,
            transport=self._transport_override,
        )

        options = _msgraph_default_options()
        options[RetryHandlerOption.get_key()] = RetryHandlerOption(
            max_retries=s.max_retries,
        )
        return GraphClientFactory.create_with_default_middleware(
            client=client,
            options=options,
        )
