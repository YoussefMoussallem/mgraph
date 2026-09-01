"""Settings contract for :mod:`m365_client`.

**This package never reads environment variables.** That is deliberate and
matches the house SDK convention: ``langfuse_client.init_client()`` takes
``public_key`` / ``secret_key`` as arguments, and the *application* owns
env parsing. Consuming services build a :class:`M365Settings` from their
own config layer -- the backend scaffold's ``_EnvSettings`` -> frozen
sub-settings pattern is the reference -- and hand it in.

Two payoffs: the package is testable without mutating ``os.environ``, and
each service keeps a single place where secrets become plain strings.

Settings are frozen so runtime code cannot mutate them, and every
validation failure raises :class:`~m365_client.errors.M365ConfigError`
at construction, so a misconfigured deployment fails at boot rather than
on the first request that touches the bad value.

Plain ``dataclasses`` rather than pydantic: ``msgraph-sdk`` already pulls
roughly ten transitive packages and does not itself need pydantic, so
adding it here would grow the dependency surface for no gain. Secret
fields use ``field(repr=False)`` -- the stdlib equivalent of pydantic's
``SecretStr`` for the case that actually matters, which is a settings
object landing in a log line or traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from m365_client.errors import M365ConfigError

__all__ = [
    "GRAPH_DEFAULT_SCOPE",
    "CacheSettings",
    "M365Settings",
]

# Client-credentials and most on-behalf-of exchanges request ``.default``,
# which means "every permission already consented for this app
# registration" rather than an ad-hoc scope list. This keeps the granted
# permission set in Entra -- where it is auditable and reviewable -- instead
# of scattered across call sites in application code.
GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

_VALID_API_VERSIONS = frozenset({"v1.0", "beta"})


@dataclass(frozen=True)
class CacheSettings:
    """Bounds for the per-user credential cache.

    On-behalf-of credentials are constructed *around* a user's assertion,
    so a fresh credential per request means a full token exchange against
    Entra on every API call. Caching credential instances is what makes
    the delegated path affordable -- but an unbounded cache in a
    long-lived multi-user service is a memory leak, hence both bounds.

    Attributes:
        max_entries: Hard ceiling on cached credentials. Least-recently-used
            entries are evicted (and closed) past this point. Size this to
            roughly the concurrent-user count you expect, not the total
            user count.
        ttl_seconds: How long a cached credential stays eligible for reuse.
            Should not exceed the lifetime of the user assertion it wraps
            (Entra access tokens are ~60-90 minutes), because a credential
            holding an expired assertion cannot refresh itself -- it needs a
            new assertion from the caller.
    """

    max_entries: int = 500
    ttl_seconds: int = 3000  # 50 min -- inside the usual assertion lifetime

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise M365ConfigError("cache max_entries must be >= 1")
        if self.ttl_seconds < 1:
            raise M365ConfigError("cache ttl_seconds must be >= 1")


@dataclass(frozen=True)
class M365Settings:
    """Everything :mod:`m365_client` needs to reach Entra and Graph.

    The app registration named by ``client_id`` plays two roles at once: it
    is the audience of the inbound user token (the API the frontend calls)
    *and* the confidential client performing the on-behalf-of exchange.
    That is why a client credential is always required, even for a service
    that only ever makes delegated calls -- during an OBO exchange the app
    authenticates itself as well as presenting the user's assertion.

    Attributes:
        tenant_id: Entra tenant (directory) id.
        client_id: Application (client) id of the app registration.
        client_secret: Client secret. Mutually exclusive with
            ``certificate_path``. Kept out of ``repr`` so a settings object
            in a log line or traceback does not leak it.
        certificate_path: Path to a PEM file holding the certificate and
            its private key. Preferred over a secret in production --
            secrets expire on a calendar and get pasted into places they
            should not be. Switching is a config change, not a code change:
            both feed the same client-credentials grant with a different
            client assertion.
        scopes: Scopes requested for Graph. Defaults to ``.default``.
        api_version: ``"v1.0"`` (default) or ``"beta"``. Beta is not
            versioned or supported for production by Microsoft -- pin to
            v1.0 unless a specific endpoint is beta-only.
        timeout_seconds: Per-request HTTP timeout. Graph can hang; an
            open-ended call ties up a worker indefinitely.
        max_retries: Ceiling for the Graph middleware's retry handler,
            which honours ``Retry-After`` on 429 and 503.
        cache: Credential cache bounds. See :class:`CacheSettings`.

    Raises:
        M365ConfigError: On any invalid or inconsistent value.
    """

    tenant_id: str
    client_id: str
    client_secret: str | None = field(default=None, repr=False)
    certificate_path: str | None = None
    scopes: tuple[str, ...] = (GRAPH_DEFAULT_SCOPE,)
    api_version: str = "v1.0"
    timeout_seconds: float = 30.0
    max_retries: int = 3
    cache: CacheSettings = field(default_factory=CacheSettings)

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise M365ConfigError("tenant_id is required")
        if not self.client_id:
            raise M365ConfigError("client_id is required")

        # Exactly one client credential. Neither means no OBO and no
        # app-only token is possible at all; both means the caller has two
        # sources of truth and we would have to silently pick one.
        has_secret = bool(self.client_secret)
        has_cert = bool(self.certificate_path)
        if not has_secret and not has_cert:
            raise M365ConfigError(
                "one of client_secret or certificate_path is required -- the app "
                "registration must authenticate itself for both the on-behalf-of "
                "exchange and the client-credentials grant"
            )
        if has_secret and has_cert:
            raise M365ConfigError(
                "client_secret and certificate_path are mutually exclusive; "
                "provide exactly one"
            )
        # Fail here rather than at first token request, where the error would
        # surface as an opaque credential failure. Same rationale as
        # ``langfuse_client.init_client`` validating ``cacert_path`` up front.
        if has_cert and not os.path.isfile(str(self.certificate_path)):
            raise M365ConfigError(
                f"certificate_path does not exist: {self.certificate_path}"
            )

        if self.api_version not in _VALID_API_VERSIONS:
            raise M365ConfigError(
                f"api_version must be one of {sorted(_VALID_API_VERSIONS)}, "
                f"got {self.api_version!r}"
            )
        if not self.scopes:
            raise M365ConfigError("scopes must not be empty")
        if self.timeout_seconds <= 0:
            raise M365ConfigError("timeout_seconds must be > 0")
        if self.max_retries < 0:
            raise M365ConfigError("max_retries must be >= 0")

    @property
    def authority(self) -> str:
        """Entra authority URL for this tenant."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"
