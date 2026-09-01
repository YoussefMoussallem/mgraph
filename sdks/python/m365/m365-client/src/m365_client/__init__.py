"""m365_client -- Microsoft 365 authentication and Graph client foundation.

Owns the parts every Microsoft 365 integration needs and none of the parts
that differ between them: token acquisition (on-behalf-of and app-only),
credential caching, a configured and retry-hardened Graph client, an error
taxonomy, and async lifecycle.

It deliberately ships **no** workload code. There is no ``get_site()``, no
``list_messages()``, no ``send_channel_message()``. SharePoint, Outlook, and
Teams calls live in the consuming service, written against the official
``GraphServiceClient`` this package hands back -- which is why that client
is returned unwrapped, with the whole typed Graph surface intact.

Typical use in a FastAPI service::

    from m365_client import M365Client, M365Settings, translate_graph_errors

    # startup
    m365 = M365Client(M365Settings(
        tenant_id=cfg.tenant_id,
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
    ))

    # per request -- acts as the signed-in user
    client = await m365.graph_for_user(assertion, user_oid)
    async with translate_graph_errors():
        messages = await client.me.messages.get()

    # shutdown
    await m365.close()

``translate_graph_errors`` is the documented calling convention, not an
optional extra: without it, callers catch Kiota's ``ODataError`` and branch
on integer status codes at every call site.
"""

from m365_client.client import M365Client
from m365_client.config import GRAPH_DEFAULT_SCOPE, CacheSettings, M365Settings
from m365_client.credentials import CredentialProvider, M365Credentials
from m365_client.errors import (
    GraphAuthError,
    GraphConflictError,
    GraphError,
    GraphInvalidRequestError,
    GraphNotFoundError,
    GraphServerError,
    GraphThrottledError,
    M365AuthError,
    M365ConfigError,
    M365Error,
    classify_status_error,
    translate_graph_errors,
    translate_graph_errors_sync,
)
from m365_client.paging import DEFAULT_TOP, MAX_TOP, check_top, collect, iter_pages

__all__ = [
    "DEFAULT_TOP",
    "GRAPH_DEFAULT_SCOPE",
    "MAX_TOP",
    "CacheSettings",
    "CredentialProvider",
    "GraphAuthError",
    "GraphConflictError",
    "GraphError",
    "GraphInvalidRequestError",
    "GraphNotFoundError",
    "GraphServerError",
    "GraphThrottledError",
    "M365AuthError",
    "M365Client",
    "M365ConfigError",
    "M365Credentials",
    "M365Error",
    "M365Settings",
    "check_top",
    "classify_status_error",
    "collect",
    "iter_pages",
    "translate_graph_errors",
    "translate_graph_errors_sync",
]
