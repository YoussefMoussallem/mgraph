"""Exception taxonomy for Microsoft 365 / Graph access.

Callers catch against this hierarchy instead of Kiota's ``APIError`` /
``ODataError`` or Azure's ``ClientAuthenticationError`` so consuming
services stay decoupled from the generated SDK's internals. Every error
carries the HTTP status where one exists, so handlers can tell transient
failures (throttling, 5xx) from permanent ones (bad scope, missing
consent, malformed request).

Hierarchy::

    M365Error
    +-- M365ConfigError               (invalid settings -- fail at boot)
    +-- M365AuthError                 (token acquisition failed)
    +-- GraphError                    (Graph returned an error)
        +-- GraphAuthError            (401/403)
        +-- GraphThrottledError       (429 -- carries retry_after)
        +-- GraphNotFoundError        (404)
        +-- GraphInvalidRequestError  (400)
        +-- GraphConflictError        (409/412 -- ETag / concurrency)
        +-- GraphServerError          (5xx)

Why this package ships a translation helper rather than translating
automatically: ``m365_client`` hands consumers a ``GraphServiceClient``
and gets out of the way -- workload calls live in the consuming service
(see the README). That means there is no interception point inside this
package, so translation is offered as an explicit context manager that
call sites wrap. It is the documented calling convention, not an
optional nicety::

    async with translate_graph_errors():
        messages = await client.me.messages.get()

Without it, every consumer ends up catching ``ODataError`` and branching
on integer status codes -- exactly the coupling this module exists to
prevent.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

__all__ = [
    "GraphAuthError",
    "GraphConflictError",
    "GraphError",
    "GraphInvalidRequestError",
    "GraphNotFoundError",
    "GraphServerError",
    "GraphThrottledError",
    "M365AuthError",
    "M365ConfigError",
    "M365Error",
    "classify_status_error",
    "translate_graph_errors",
    "translate_graph_errors_sync",
]


class M365Error(Exception):
    """Base exception for everything this package raises.

    Holds the HTTP status when one is known so downstream handlers can
    decide whether to retry, surface to the caller, or page oncall.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class M365ConfigError(M365Error):
    """Settings are missing or internally inconsistent.

    Raised while building settings, before any network call, so a
    misconfigured deployment fails at boot rather than on the first
    request that happens to touch the bad value. Never retry.
    """


class M365AuthError(M365Error):
    """Token acquisition failed -- the Graph call was never attempted.

    Distinct from :class:`GraphAuthError`, which means Graph itself
    rejected a token we successfully obtained. This one covers the
    on-behalf-of exchange and client-credentials grant failing at Entra:
    missing admin consent, an unexposed API scope, a wrong client
    secret, an expired certificate, or an assertion Entra will not
    accept.

    ``aadsts_code`` and ``correlation_id`` are surfaced verbatim when
    Entra provides them -- these are exactly what Microsoft support asks
    for, and swallowing them turns a five-minute diagnosis into an
    afternoon. Do not retry: every cause is a config or input problem.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        aadsts_code: str | None = None,
        correlation_id: str | None = None,
    ):
        self.aadsts_code = aadsts_code
        self.correlation_id = correlation_id
        super().__init__(message, status_code=status_code)


class GraphError(M365Error):
    """Microsoft Graph returned an error response.

    ``graph_code`` is Graph's own machine-readable error code (e.g.
    ``itemNotFound``, ``activityLimitReached``) which is finer-grained
    than the HTTP status and worth logging.  ``request_id`` is Graph's
    server-side request id -- quote it in support tickets.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        graph_code: str | None = None,
        request_id: str | None = None,
    ):
        self.graph_code = graph_code
        self.request_id = request_id
        super().__init__(message, status_code=status_code)


class GraphAuthError(GraphError):
    """Graph rejected the token, or the token lacks the needed scope (401/403).

    A 403 here usually means the permission was never granted or consented,
    not that the user lacks access to the resource. Do not retry -- fix the
    app registration's permissions.
    """


class GraphThrottledError(GraphError):
    """Graph throttled the request (429).

    The SDK's middleware already retried per ``Retry-After`` before this
    surfaced, so seeing it means the retry budget was exhausted. Back off
    well beyond ``retry_after`` and consider reducing concurrency --
    Graph throttles per-workload, and hammering a throttled endpoint
    extends the penalty.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        graph_code: str | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
    ):
        self.retry_after = retry_after
        super().__init__(
            message,
            status_code=status_code,
            graph_code=graph_code,
            request_id=request_id,
        )


class GraphNotFoundError(GraphError):
    """Resource does not exist, or is invisible to this caller (404).

    Graph deliberately returns 404 rather than 403 for some resources the
    caller may not see, so do not treat this as proof of non-existence.
    Do not retry.
    """


class GraphInvalidRequestError(GraphError):
    """Malformed request -- bad query, unsupported parameter, invalid body (400).

    Retrying unchanged will keep failing. Surface to the caller so the
    request can be corrected.
    """


class GraphConflictError(GraphError):
    """Concurrency or state conflict (409, 412).

    A 412 is an ``If-Match``/ETag precondition failure: someone else
    modified the item since you read it. This is routine in SharePoint and
    Outlook rather than exceptional, which is why it gets its own type --
    the correct response is to re-read the item and retry the write, not
    to fail the request.
    """


class GraphServerError(GraphError):
    """Graph-side failure (5xx).

    Usually transient; retry with backoff. Alert if it persists, and check
    the Microsoft 365 service health dashboard before debugging your own
    code.
    """


# Graph's ``code`` values that mean "throttled" even when the transport
# status is not a clean 429 (e.g. a 503 carrying activityLimitReached).
_THROTTLE_CODES = frozenset(
    {
        "activityLimitReached",
        "quotaLimitReached",
        "serviceNotAvailable",
        "requestThrottled",
    }
)


def classify_status_error(
    status_code: int,
    message: str,
    *,
    graph_code: str | None = None,
    request_id: str | None = None,
    retry_after: int | None = None,
) -> GraphError:
    """Map an HTTP status to the right :class:`GraphError` subclass.

    Used at the boundary so application code never imports Kiota
    exceptions or branches on integer status codes directly. Mirrors
    ``llm_provider.exceptions.classify_status_error`` in shape so the two
    SDKs read the same way.

    ``graph_code`` is consulted for the throttling case only: Graph
    sometimes signals throttling as a 503 carrying
    ``activityLimitReached``, and treating that as a plain server error
    loses the ``retry_after`` the caller needs.
    """
    common = {"graph_code": graph_code, "request_id": request_id}

    if status_code == 429 or (graph_code in _THROTTLE_CODES):
        return GraphThrottledError(
            message,
            status_code=status_code,
            retry_after=retry_after,
            **common,
        )
    if status_code in (401, 403):
        return GraphAuthError(message, status_code=status_code, **common)
    if status_code == 404:
        return GraphNotFoundError(message, status_code=status_code, **common)
    if status_code in (409, 412):
        return GraphConflictError(message, status_code=status_code, **common)
    if status_code == 400:
        return GraphInvalidRequestError(message, status_code=status_code, **common)
    if status_code >= 500:
        return GraphServerError(message, status_code=status_code, **common)
    return GraphError(message, status_code=status_code, **common)


def _header(headers: Any, name: str) -> str | None:
    """Read one header from whatever shape the SDK handed us.

    Kiota types ``response_headers`` as ``dict[str, str]``, but the value
    that actually arrives can be a ``HeadersCollection`` or a mapping whose
    values are lists. Header lookup is also case-insensitive on the wire.
    Being tolerant here is cheap; guessing wrong loses the ``Retry-After``
    a throttled caller needs.
    """
    if not headers:
        return None

    getter = getattr(headers, "get", None)
    if getter is None:
        return None

    value = None
    for candidate in (name, name.lower(), name.title(), name.upper()):
        try:
            value = getter(candidate)
        except TypeError:
            return None
        if value:
            break

    if not value:
        return None
    # HeadersCollection returns a set/list of values per header.
    if isinstance(value, (list, tuple, set)):
        value = next(iter(value), None)
    return str(value) if value is not None else None


def _int_or_none(value: str | None) -> int | None:
    """Parse an integer header, degrading to None rather than raising.

    A malformed ``Retry-After`` must not turn a useful throttling error into
    an unrelated ``ValueError`` from inside the translation layer.
    """
    if value is None:
        return None
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


def _entra_auth_error(exc: Exception) -> M365AuthError:
    """Translate an Entra credential failure into :class:`M365AuthError`.

    Extracts the ``AADSTS`` code and correlation id when present, because
    those are exactly what Microsoft support asks for -- dropping them turns
    a five-minute diagnosis into an afternoon of guessing.

    The parsing is deliberately loose: Entra's error text is not a
    documented format, so we scrape what is reliably there and keep the
    original message verbatim rather than pretending to parse it fully. No
    code-to-remediation mapping is baked in yet -- hardcoding ``AADSTS``
    numbers from memory produces confidently wrong hints, which are worse
    than none. Build that table from codes actually observed in practice.
    """
    message = str(exc)

    # Entra writes the code as "AADSTS65001:" at the head of the message, so
    # the trailing colon has to come off too.
    _punct = ".;,:()[]'\""
    aadsts_code = next(
        (
            token.strip(_punct)
            for token in message.replace(",", " ").split()
            if token.strip(_punct).startswith("AADSTS")
        ),
        None,
    )

    correlation_id = None
    lowered = message.lower()
    label = "correlation id"
    if label in lowered:
        tail = message[lowered.index(label) + len(label) :]
        for token in tail.replace(":", " ").split():
            candidate = token.strip(_punct)
            if candidate.count("-") == 4 and len(candidate) >= 32:
                correlation_id = candidate
                break

    return M365AuthError(
        message,
        aadsts_code=aadsts_code,
        correlation_id=correlation_id,
    )


def _translate(exc: Exception) -> M365Error | None:
    """Map one SDK exception to ours, or None if it is not ours to map.

    Split out from the context manager so it can be unit-tested directly
    against synthetic exceptions, without driving a real Graph call.
    """
    # Imported lazily and defensively: these are heavy generated modules,
    # and a version bump that moves them must degrade to "pass the original
    # through" rather than breaking every call site with an ImportError.
    try:
        from azure.core.exceptions import ClientAuthenticationError
        from kiota_abstractions.api_error import APIError
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError
    except ImportError:  # pragma: no cover - defensive
        return None

    # Entra refused to issue a token, so Graph was never reached. Checked
    # first because it is not an APIError and means something different: a
    # config problem, not a permissions problem.
    if isinstance(exc, ClientAuthenticationError):
        return _entra_auth_error(exc)

    if not isinstance(exc, APIError):
        return None

    status = getattr(exc, "response_status_code", None)
    headers = getattr(exc, "response_headers", None)

    graph_code = None
    message = getattr(exc, "message", None) or str(exc)

    if isinstance(exc, ODataError):
        main = getattr(exc, "error", None)
        if main is not None:
            graph_code = getattr(main, "code", None)
            # Graph's own message is more specific than Kiota's generic
            # "the server returned an unexpected status code".
            detail = getattr(main, "message", None)
            if detail:
                message = detail

    # A status of 0 or None means the request never got a response
    # (connection failure, DNS, timeout). Treat as a server-side problem so
    # callers retry rather than treating it as a permanent 4xx.
    if not status:
        return GraphServerError(
            message or "Graph request failed without a response",
            status_code=None,
            graph_code=graph_code,
            request_id=_header(headers, "request-id"),
        )

    return classify_status_error(
        int(status),
        message or f"Graph returned {status}",
        graph_code=graph_code,
        request_id=_header(headers, "request-id"),
        retry_after=_int_or_none(_header(headers, "Retry-After")),
    )


@contextlib.asynccontextmanager
async def translate_graph_errors() -> AsyncIterator[None]:
    """Translate SDK exceptions raised inside the block into this taxonomy.

    The documented calling convention for every Graph call::

        async with translate_graph_errors():
            messages = await client.me.messages.get()

    Raises :class:`GraphThrottledError`, :class:`GraphNotFoundError`, and so
    on instead of ``ODataError``, and :class:`M365AuthError` instead of
    ``ClientAuthenticationError``. Exceptions it does not recognise -- your
    own ``ValueError``, ``asyncio.CancelledError`` -- pass through
    untouched.

    Cancellation is never translated -- a caller must be able to tell its
    own abort from a Graph failure. No carve-out is needed for that:
    ``asyncio.CancelledError`` derives from ``BaseException``, not
    ``Exception``, so it passes straight through the handler below.
    """
    try:
        yield
    except Exception as exc:
        translated = _translate(exc)
        if translated is None:
            raise
        raise translated from exc


@contextlib.contextmanager
def translate_graph_errors_sync() -> Iterator[None]:
    """Synchronous twin of :func:`translate_graph_errors`.

    For the rare sync call site -- a management script, a migration -- that
    still wants the same taxonomy. Prefer the async form everywhere in
    service code.
    """
    try:
        yield
    except Exception as exc:
        translated = _translate(exc)
        if translated is None:
            raise
        raise translated from exc
