# m365-client

Microsoft 365 authentication and Graph client foundation for AI Labs services.
Owns what every Microsoft 365 integration needs and nothing that differs
between them.

| | |
| --- | --- |
| **PyPI name** | `m365-client` |
| **Import** | `m365_client` |
| **Version** | 0.1.0 |
| **Python** | >= 3.11 |
| **Consumed by** | [`outlook-client`](../outlook-client/), [`sharepoint-client`](../sharepoint-client/), and future Teams integrations |

## What it does and does not do

| Owns | Deliberately absent |
| --- | --- |
| Token acquisition — on-behalf-of and app-only | Any Graph call |
| Credential caching, bounded by size and age | `get_site()`, `list_messages()`, `send_channel_message()` |
| A configured, retry-hardened `GraphServiceClient` | Workload models or DTOs |
| Error taxonomy and translation | Business logic |
| Async lifecycle | Environment variable reading |

There is no `sharepoint.py`, `outlook.py`, or `teams.py` here, by design.
Workload calls live in the sibling workload SDKs —
[`outlook-client`](../outlook-client/) and
[`sharepoint-client`](../sharepoint-client/) — and in consuming apps, written
against the **official** `GraphServiceClient` this package returns —
unwrapped, with the whole typed Graph surface intact. Wrapping it would only
take capability away.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client        # from a clone
```

Published wheels come from the `m365-client-v*` release tag. See
[Python SDK installation](../../docs/installation.md) for the Artifactory feed.

## Quickstart

```python
from m365_client import M365Client, M365Settings, translate_graph_errors

# ── startup: build once, share across requests ──────────────────────
m365 = M365Client(M365Settings(
    tenant_id=cfg.tenant_id,
    client_id=cfg.client_id,
    client_secret=cfg.client_secret,   # or certificate_path=...
))

# ── per request: act as the signed-in user ──────────────────────────
client = await m365.graph_for_user(assertion, user_oid)
async with translate_graph_errors():
    messages = await client.me.messages.get()

# ── background work with no user present ────────────────────────────
app_client = await m365.graph_for_app()

# ── shutdown ────────────────────────────────────────────────────────
await m365.close()
```

Three symbols cover the common path: `M365Client`, `M365Settings`,
`translate_graph_errors`. The rest is paging (below) and env mapping
(`m365_client.env`). The HTTP side — validating the caller's token and turning
it into a Graph client per request — belongs to the consuming app, and the
[backend scaffold](../../../../scaffolds/backend/) ships it ready-made in
`app/graph.py`.

## The input contract, which trips everyone once

`graph_for_user()` needs an **access token issued for your service's own API
scope** — `api://<client-id>/access_as_user`. An **ID token will not work**;
Entra rejects it as an on-behalf-of assertion.

That requires three things outside this package:

1. The app registration **exposes an API scope**.
2. Graph **delegated permissions** are added and consented.
3. The calling client **requests that scope** and sends its access token, not
   its ID token.

Entra's own error for this is an `AADSTS`-coded 400 that reads like a signature
or audience problem, so the SDK preflights the assertion instead: it checks for
the `scp` claim (present on access tokens, absent on ID tokens — `aud` does not
discriminate, since both carry the client id) and fails in microseconds with a
message naming the fix.

## Authentication

### On-behalf-of — the default

Acts as the signed-in user, so **Graph enforces that user's own permissions**.
This is what [the architecture standard prefers][arch]: delegated access avoids
the broad tenant consent enterprises push back on, and it keeps application
code from being the only thing between a user and the whole tenant.

### App-only — where no user exists

For the three things on-behalf-of structurally cannot do:

1. Creating and renewing change-notification subscriptions.
2. Posting as an app rather than a user.
3. Background jobs with no user token — e.g. SharePoint indexing for SS-05.

Prefer delegated whenever a user is present. App-only permissions are
tenant-wide, and unlike the database path there is no RLS backstop — Graph *is*
the store, and you have told it you may read everything.

### Certificate is not a third flow

Same client-credentials grant, different client assertion. Set
`certificate_path` instead of `client_secret` and nothing else changes, so
hardening production is a config change and a deployment rather than a release.

### A client credential is always required

Even for a service that only ever makes delegated calls. An on-behalf-of
exchange is a confidential-client operation — the app authenticates itself as
well as presenting the user's assertion.

## Credential caching

The delegated path is only affordable because credentials are cached.
`OnBehalfOfCredential` is constructed *around* a specific assertion, so
building one per request means a full token exchange against Entra on every
API call.

Cache key: `(user_id, truncated hash of the assertion)`.

- The **user id** identifies the principal.
- The **assertion hash** means a silently-refreshed caller token produces a
  fresh credential rather than reusing one wrapped around an expired
  assertion. It is hashed, not stored — the raw token is a live bearer
  credential and cache contents surface in heap dumps.

Bounded on both axes via `CacheSettings`: LRU past `max_entries`, and
`ttl_seconds` (default 3000s, inside the usual 60–90 minute assertion
lifetime). Evicted and expired credentials are **closed**, not just dropped —
otherwise each eviction leaks a socket, slowly and invisibly.

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

## Error handling

```
M365Error
├── M365ConfigError               invalid settings — fails at boot, never retry
├── M365AuthError                 token acquisition failed; Graph never reached
│                                 carries aadsts_code + correlation_id
└── GraphError                    Graph returned an error
    ├── GraphAuthError            401/403 — permission not granted; do not retry
    ├── GraphThrottledError       429 — carries retry_after
    ├── GraphNotFoundError        404 — do not retry
    ├── GraphInvalidRequestError  400 — fix the input
    ├── GraphConflictError        409/412 — ETag conflict; re-read and retry
    └── GraphServerError          5xx — retry with backoff
```

`M365AuthError` and `GraphAuthError` are deliberately unrelated: the first
means Entra refused to issue a token (a config or consent problem), the second
means Graph rejected a token we did obtain (a permissions problem). A handler
that conflates them cannot tell those apart.

`GraphConflictError` exists because 412 `If-Match` failures are routine in
SharePoint and Outlook rather than exceptional — the right response is to
re-read and retry the write, not to fail the request.

### `translate_graph_errors()` is the calling convention

```python
async with translate_graph_errors():
    messages = await client.me.messages.get()
```

Not optional in practice. Because this package hands back the client and steps
out of the call path, it has no interception point — translation cannot be
automatic. Without the context manager, every consumer catches Kiota's
`ODataError` and branches on integer status codes at each call site, which is
exactly the coupling the taxonomy exists to prevent.

A synchronous twin, `translate_graph_errors_sync()`, covers management scripts.

Cancellation is never translated: `asyncio.CancelledError` derives from
`BaseException`, so it passes straight through and a caller can always tell its
own abort from a Graph failure.

## What comes free, and what cannot

Anything configurable at construction time is applied for you:

- **Retry and throttling** honouring `Retry-After` on 429 and 503. This is the
  strongest argument for the official SDK — Graph throttles aggressively and
  per-workload, and a hand-rolled retry loop that ignores `Retry-After` gets
  throttled harder.
- Redirect handling, telemetry headers, compression.
- Timeouts (`timeout_seconds`, default 30). Graph can hang, and an open-ended
  call ties up a worker indefinitely.

Anything requiring call interception — error translation, tracing spans — is
opt-in, for the reason above.

### Two upstream gotchas this package absorbs

Recorded because all three fail *silently*, and all three bite anyone who
configures the Graph client themselves.

1. **The `/me` rewrite is conditional middleware.**
   `GraphServiceClient.me` is generated as
   `users.by_user_id("me-token-to-replace")` and only becomes `/me` via a
   `UrlReplaceHandlerOption` that `GraphRequestAdapter` installs *only when it
   builds its own httpx client*. Supplying your own — which you must, to set
   timeouts — sends every `client.me.*` call to a literal placeholder URL. This
   package reuses msgraph's own options dict rather than reimplementing it, so
   the rewrite stays correct across SDK upgrades.

2. **`base_url` gains a trailing slash and Kiota concatenates.** httpx
   normalises `base_url` to end in `/`; Kiota reads it back and expands
   `{+baseurl}/users/...`, producing `…/v1.0//users/…`. The adapter's
   `base_url` is therefore set explicitly, un-slashed.

3. **Kiota closes the credential after *every* token acquisition.**
   `AzureIdentityAccessTokenProvider.get_authorization_token` ends with
   `await self._credentials.close()`. Harmless for the credential-per-call
   usage the generated SDK assumes; corrosive for a cached credential, and
   invisible at first because MSAL serves later reads from its in-memory cache
   — the failure surfaces only when a refresh is finally needed, roughly an
   hour into a deployment. Cached credentials are wrapped so that `close()` is
   a no-op and only this package's lifecycle really closes them.

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

Async credentials own HTTP sessions. `close()` is idempotent and safe on an
unused instance; skipping it produces "Unclosed client session" warnings on
every restart and leaks sockets under load.

## Configuration

This package **never reads environment variables** — the house SDK convention
(`langfuse_client.init_client()` takes explicit arguments). The consuming
service owns env parsing and passes a frozen `M365Settings`. That keeps the
package testable without mutating `os.environ` and keeps secrets in one place
per service. `m365_client.env.settings_from_env()` is the explicit, opt-in
helper for services that want the house mapping (`AZURE_TENANT_ID`,
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` / `AZURE_CERTIFICATE_PATH`, and the
`M365_*` tuning variables) rather than their own.

`client_secret` is excluded from `repr`, so a settings object in a log line or
traceback does not leak it.

## Corporate networks

`transport=` is the seam: it swaps the bottom of the HTTP stack while
everything above it — the Graph middleware, base URL, timeouts, the `/me`
rewrite — stays in place.

```python
M365Client(settings, transport=httpx.AsyncHTTPTransport(verify=ca_bundle, proxy=proxy))
```

Injecting a whole `httpx.AsyncClient` is deliberately not offered — it bypasses
the Graph middleware, and losing the `/me` rewrite that way fails at runtime
rather than at construction.

This covers the **Graph** stack only. `azure-identity` reaches Entra over its
own transport, configured through the usual `HTTPS_PROXY` / `SSL_CERT_FILE`
variables, and the caller-token validation in the backend scaffold resolves
its JWKS CA bundle from `SSL_CERT_FILE`, then `REQUESTS_CA_BUNDLE`, then
`certifi`. Never commit PEM
files — point an environment variable at a path on disk.

## Dependencies

| Package | Why |
| --- | --- |
| `msgraph-sdk` | Official Microsoft Graph SDK. Since this package ships no workload helpers, consumers need the full typed Graph surface, and generated models provide it. Also brings Graph's own throttling middleware. Costs ~10 transitive Kiota packages and a slow cold import — fine for long-lived services, wrong for serverless. |
| `azure-identity` | Credential flows. Only `azure.identity.aio` is used; the synchronous classes block the event loop ([CONTRIBUTING §3][contrib]). |

No pydantic: `msgraph-sdk` does not need it, so adding it would grow the
dependency surface for no gain. Settings use frozen dataclasses.

## Deferred: delegated access from stored refresh tokens

The `CredentialProvider` protocol defines the seam for a third flow — delegated
access using the per-user encrypted refresh tokens in Identity's
`idp_oauth_token` table, for offline work on a user's behalf.

Not implemented, deliberately: `services/identity/` is currently a
README-only stub with no endpoint to read a token from and no decryption
contract. Defining the shape now is free; building against an interface that
does not exist yet means untestable code that gets rewritten when the real
contract lands.

## Known constraints, worth planning around

- **`Sites.Selected` provisioning.** App-only `Sites.Read.All` is tenant-wide
  and enterprises reject it. `Sites.Selected` is the answer but requires a
  per-site grant step — an operational process needing an owner before
  background SharePoint indexing can ship.
- **App-only Teams channel messaging is gated.** It is a Microsoft *protected
  API* needing either an approved request or RSC via a Teams app manifest.
  Approval takes time you cannot compress; start it early.
- **Python 3.14.** CI installs on 3.11–3.14. Not every transitive Kiota package is
  confirmed to publish 3.14 wheels; if a leg goes red for that reason, exclude
  this package from it with a comment rather than pinning blindly.

[arch]: ../../../../docs/architecture/foundation-services-and-sharing-framework.md
[contrib]: ../../../../CONTRIBUTING.md
