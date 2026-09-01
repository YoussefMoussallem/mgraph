# Authentication & token flows

Two flows, one factory. Delegated is the default; app-only exists for the
narrow set of things delegated access structurally cannot do.

## The input contract, which trips every team once

`graph_for_user()` requires an **access token issued for your service's own API
scope** — `api://<client-id>/access_as_user`. An **ID token will not work**:
Entra rejects it as an on-behalf-of assertion outright.

Three things outside this package have to be true:

1. The app registration **exposes an API scope**.
2. Graph **delegated permissions** are added and consented.
3. The calling client **requests that scope** and sends its *access* token.

Point 3 is where the time goes. Entra's native error is an `AADSTS`-coded 400
that reads like a signature or audience problem, so the SDK preflights instead:

```python
# raises M365AuthError in microseconds, before any call to Entra:
# "assertion looks like an ID token (no 'scp' claim), which Entra will not
#  accept as an on-behalf-of assertion. …"
await m365.graph_for_user(id_token, oid)
```

`scp` is the discriminator, not `aud`. An ID token and an access token for your
own API both carry the client id as audience; only an access token carries a
scope claim. A token with `roles` but no `scp` is an app-only token, which
on-behalf-of also cannot use, and gets its own message.

!!! note "Prerequisite, not a blocker"
    Nothing above blocks *building* against this SDK — the `credentials=` and
    `transport=` seams on `M365Client` let a consumer stand in for Entra and
    Graph. It is only needed for the first end-to-end run, so Entra
    configuration can proceed in parallel.

## Delegated — on-behalf-of

```python
client = await m365.graph_for_user(assertion, user_oid)
```

The service exchanges the caller's token and acts **as that user**, so Graph
itself enforces what they may see.

This is what [the architecture standard prefers][arch]: delegated access avoids
the broad tenant consent enterprises push back on. The deeper reason is that
delegated access is permission-trimmed by construction. Call app-only with
`Sites.Read.All` and your application code becomes the *only* thing between a
user and every document in the tenant — and unlike the database path there is
no RLS backstop available, because Graph is the store and you have told it you
may read everything.

## App-only — client credentials

```python
client = await m365.graph_for_app()
```

For the three things on-behalf-of cannot do:

1. **Change-notification subscriptions** — created and renewed on a schedule
   with no user request in flight.
2. **Posting as an app** rather than as a user.
3. **Background jobs** with no user token, e.g. SharePoint indexing for SS-05's
   autoscaled ingestion workers.

Prefer delegated whenever a user is present.

## Certificate is not a third flow

`ClientSecretCredential` and `CertificateCredential` are the same
client-credentials grant with a different client assertion, so this is a branch
on settings, not a separate code path:

```python
M365Settings(tenant_id=..., client_id=..., certificate_path="/run/secrets/app.pem")
```

Hardening production from secret to certificate becomes a config change and a
deployment rather than a release.

!!! warning "An asymmetry in azure-identity"
    `CertificateCredential` takes a certificate **path**;
    `OnBehalfOfCredential` takes certificate **bytes** — same PEM file, two
    parameter shapes. The SDK handles both; worth knowing if you read the code.

## A client credential is always required

Even for a service that only ever makes delegated calls. An on-behalf-of
exchange is a confidential-client operation: the app authenticates itself as
well as presenting the user's assertion. Omitting both `client_secret` and
`certificate_path` raises `M365ConfigError` at construction.

## Credential caching

The delegated path is only affordable because credentials are cached.
`OnBehalfOfCredential` is constructed *around* a specific assertion, so
building one per request means a full token exchange against Entra on **every
API call**.

App-only is the easy case: one credential for the process lifetime, built
lazily on first use, with `azure-identity` refreshing the token internally.

Delegated is cached per user:

| Cache key part | Why |
|---|---|
| `user_id` | Identifies the principal. |
| Truncated SHA-256 of the assertion | A silently-refreshed caller token yields a *fresh* credential rather than reusing one wrapped around an expired assertion. Hashed rather than stored because the raw token is a live bearer credential and cache contents surface in heap dumps and debuggers. |

Bounded on both axes:

```python
M365Settings(..., cache=CacheSettings(max_entries=500, ttl_seconds=3000))
```

- `max_entries` — LRU eviction past this point. Size to expected *concurrent*
  users, not total users. An unbounded cache in a long-lived multi-user service
  is a memory leak.
- `ttl_seconds` — default 3000 (50 min), deliberately inside the usual 60–90
  minute assertion lifetime. A cached credential cannot refresh itself once its
  assertion expires; it needs a new one from the caller.

Evicted and expired credentials are **closed**, not merely dropped. Each holds
an open HTTP session, so dropping without closing leaks a socket per eviction —
slowly and invisibly, until a service exhausts its file descriptors days later.

Concurrent first-use for the same cold user is guarded by an async lock with
double-checked locking, so twenty simultaneous requests produce one token
exchange rather than twenty, with nineteen orphaned credentials.

## Kiota closes your credential on every call

Worth knowing because it is invisible until it is not.
`AzureIdentityAccessTokenProvider.get_authorization_token` ends with:

```python
if inspect.isawaitable(result):
    result = await result
    await self._credentials.close()      # every single request
```

Harmless for the credential-per-call usage the generated SDK assumes.
Corrosive for a *cached* credential: its transport is torn down while we still
hold the object. MSAL's in-memory token cache then hides the damage — reads
keep succeeding, and the failure only surfaces when a refresh is finally
needed, roughly an hour into a deployment and far from the cause.

The SDK wraps cached credentials so `close()` is a no-op that absorbs Kiota's
call, and only its own lifecycle really closes them. Nothing to configure; it
is pinned by tests.

## Deferred: delegated access from stored refresh tokens

The `CredentialProvider` protocol defines the seam for a third flow — delegated
access using the per-user encrypted refresh tokens in Identity's
`idp_oauth_token` table, for offline work on a user's behalf.

Not implemented, deliberately. `services/identity/` is currently a README-only
stub with no endpoint to read a token from and no decryption contract. Defining
the shape now costs nothing; building against an interface that does not exist
means untestable code that gets rewritten when the real contract lands.

## Permission constraints worth planning around

- **`Sites.Selected`.** App-only `Sites.Read.All` is tenant-wide and
  enterprises reject it. `Sites.Selected` scopes app-only access to explicitly
  granted sites, but requires a per-site grant step — an operational process
  needing an owner before background SharePoint indexing can ship.
- **App-only Teams channel messaging is gated outside your control.** It is a
  Microsoft *protected API* requiring either an approved request or RSC via a
  Teams app manifest. Approval takes time you cannot compress; start it in
  parallel with development.

[arch]: https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/docs/architecture/foundation-services-and-sharing-framework.md
