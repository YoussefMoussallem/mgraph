# Corporate networks

One constructor argument, `transport=`, swaps the bottom of the HTTP stack
while everything above it stays in place: base URL, timeouts, retry handling,
the `/me` URL rewrite, generated request builders, deserialisation, and error
translation. Only the socket layer is replaced.

```python
transport = httpx.AsyncHTTPTransport(
    verify="/etc/ssl/certs/corporate-ca.pem",
    proxy="http://proxy.corp.example:8080",
)
m365 = M365Client(settings, transport=transport)
```

## Why a whole `httpx.AsyncClient` cannot be injected

Deliberately not offered. `GraphRequestAdapter` installs the middleware that
rewrites `/users/me-token-to-replace` → `/me` **only when it builds its own
client**. `GraphServiceClient.me` is generated as
`users.by_user_id("me-token-to-replace")` and depends on that rewrite, so a
client supplied from outside sends every `client.me.*` call to a literal
placeholder URL — silently, at runtime, rather than failing at construction.

The `transport` seam gives the same control without that trap.

## Three stacks, not one

`transport=` covers the **Graph** stack only. Two more connections leave the
process, each with its own configuration:

- `azure-identity` reaches Entra for the on-behalf-of exchange and the
  client-credentials grant over its own transport. Proxy and CA settings for
  that come from the usual `HTTPS_PROXY` / `SSL_CERT_FILE` environment
  variables.
- Validating the *caller's* token fetches Microsoft's JWKS — that is the
  app's job, and the [backend scaffold](https://pwc-me-adv-strategyand.github.io/infra-platform-services/scaffolds/backend/)'s `app/dependencies.py`
  resolves its CA bundle from `SSL_CERT_FILE`, then `REQUESTS_CA_BUNDLE`, then
  `certifi`, so a deployment behind a TLS-inspecting proxy works without
  patching anything.

Never commit PEM files — point an environment variable at a path on disk.

## The other seam

`credentials=` replaces token acquisition. `M365Client` accepts any object
satisfying the `CredentialProvider` protocol, which is how a future credential
source — delegated access from stored refresh tokens, for example — plugs in
without touching the client layer. See
[Authentication & token flows](authentication.md).
