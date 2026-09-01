# m365-client — getting started

Microsoft 365 authentication and Graph client foundation. The design goal:
**own what every Microsoft 365 integration needs and nothing that differs
between them**, so the Outlook and SharePoint SDKs — and a future Teams one
— share one auth story without sharing workload code.

## What it deliberately does not contain

No `get_site()`, no `list_messages()`, no `send_channel_message()`. Workload
calls belong to the workload SDKs — [outlook-client](../outlook-client/index.md)
and [sharepoint-client](../sharepoint-client/index.md) — and to consuming
services, written against the **official** `GraphServiceClient` this package
hands back — unwrapped, with the whole typed Graph surface intact. Wrapping it
would only remove capability.

What the package owns instead: token acquisition, credential caching, a
configured and retry-hardened client, an error taxonomy, and async lifecycle.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client
```

See [Installation](../installation.md) for the Artifactory feed.

## Three symbols

```python
from m365_client import M365Client, M365Settings, translate_graph_errors

# startup — build once, share across requests
m365 = M365Client(M365Settings(
    tenant_id=cfg.tenant_id,
    client_id=cfg.client_id,
    client_secret=cfg.client_secret,
))

# per request — acts as the signed-in user
client = await m365.graph_for_user(assertion, user_oid)
async with translate_graph_errors():
    messages = await client.me.messages.get()

# shutdown
await m365.close()
```

## Paging

Graph pages every collection through `@odata.nextLink`, and the loop that
follows it is protocol plumbing rather than workload code, so it lives here
once: `iter_pages()` streams items across pages, `collect()` materialises a
bounded list. The request configuration is applied to the first page only —
the next link already encodes `$select`, `$top` and friends, and re-sending
them is an error.

```python
messages = await collect(
    client.me.messages,
    lambda b: b.get(request_configuration=config),
    max_items=50,
)
```

`MAX_TOP` (50) is the platform rule that no list call returns more than 50
items, and `check_top()` is how the workload SDKs enforce it before any
network call — one number, one place.

## Settings

`M365Settings` is a frozen dataclass, validated at construction so a
misconfigured deployment fails at boot rather than on the first request that
touches the bad value.

| Field | Purpose |
|---|---|
| `tenant_id` / `client_id` | Entra tenant and app registration. Required. |
| `client_secret` | Client secret. Excluded from `repr` so a settings object in a log line does not leak it. |
| `certificate_path` | PEM with certificate and private key. Mutually exclusive with `client_secret`; exactly one is required. |
| `scopes` | Defaults to `https://graph.microsoft.com/.default`, which means "everything already consented for this app" — keeping the granted set auditable in Entra rather than scattered across call sites. |
| `api_version` | `v1.0` (default) or `beta`. |
| `timeout_seconds` | Default 30. Graph can hang, and an open-ended call ties up a worker indefinitely. |
| `max_retries` | Default 3. Ceiling for the middleware that honours `Retry-After`. |
| `cache` | `CacheSettings(max_entries, ttl_seconds)` — see [Authentication](authentication.md). |

**This package never reads environment variables.** That matches the house SDK
convention (`langfuse_client.init_client()` takes explicit arguments): the
*application* owns env parsing and hands in settings. The package stays
testable without mutating `os.environ`, and secrets exist as plain strings in
exactly one place per service.

## Lifecycle

```python
@asynccontextmanager
async def lifespan(app):
    app.state.m365 = M365Client(settings)
    try:
        yield
    finally:
        await app.state.m365.close()
```

Build once. Per-request construction throws away the credential cache, which
is the only thing making the delegated path affordable. Async credentials own
HTTP sessions, so skipping `close()` produces "Unclosed client session"
warnings on every restart and leaks sockets under load. `close()` is idempotent
and safe on an unused instance.

## Where to go next

- [Authentication & token flows](authentication.md) — on-behalf-of, app-only,
  certificates, and the caching design
- [Errors & retries](errors.md) — the taxonomy and the calling convention
- [Corporate networks](corporate-network.md) — proxies, private CAs, and the
  transport seam
- [outlook-client](../outlook-client/index.md) and
  [sharepoint-client](../sharepoint-client/index.md) — the workload SDKs
  built on this one
