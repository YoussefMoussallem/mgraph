# Corporate proxy / private CA

On a TLS-intercepting network (Zscaler, an internal gateway with a private
CA), Langfuse traffic fails certificate verification against the default
trust store. Two logical transports are affected:

1. **REST/ingestion API** (`@langfuse/client`) — API calls and media.
2. **Span export** (`@langfuse/otel`'s `LangfuseSpanProcessor`) — carries
   the actual traces, from a background batch task.

> [!WARNING]
> **The silent half.** Span export fails in the *background* — your code
> throws nothing and API-side calls can still look healthy, while traces
> never arrive in Langfuse. This split cost real debugging time in
> production (in the Python deployment this SDK descends from); after any
> trust-store change, verify traces actually arrive rather than assuming
> "no errors" means "working".

## The honest Node divergence

This is the one place the TypeScript SDK deliberately diverges from Python
(recorded in [PARITY.md](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/PARITY.md)). Python's `cacert_path` fully
solves the problem in-process: httpx accepts a per-client CA bundle
(`verify=`), and the requests-based OTLP exporter reads the OTel certificate
env vars. In Node, **the `@langfuse` JS SDK v5 has no first-class CA
option** on either transport — both ride Node's fetch stack (undici), which
trusts Node's bundled CA store plus whatever the *process-level* knob adds.

So the knobs are:

- **`NODE_EXTRA_CA_CERTS` — the trust knob that actually does the work.**
  Point it at the corp CA bundle *before the Node process starts* (it is
  read once at startup; setting `process.env` later does nothing). It
  appends the CA to Node's trust store for all TLS in the process — both
  Langfuse transports, and anything else that must traverse the intercepting
  proxy:

  ```bash
  NODE_EXTRA_CA_CERTS=/etc/ssl/certs/corp-ca.pem node dist/main.js
  ```

- **`cacertPath` — validated, and exported via the OTel env-var rule.**
  `initClient` throws immediately if the path is not an existing file (fail
  fast on a bad certificate mount, before anything half-initialises), then
  sets `OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE` to it — **only if the
  deployment configured neither OTel certificate variable**
  (`OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE` or
  `OTEL_EXPORTER_OTLP_CERTIFICATE`). The traces-scoped var outranks the
  general one, so platform-level OTel config always wins, and exporters for
  other signals (metrics, logs) are never touched — the same precedence
  contract as the Python SDK. Be clear about what this buys today: OTel-
  conformant OTLP exporters honor that env var, but it does **not**
  configure Node's fetch TLS — so `cacertPath` alone does not fix
  certificate failures the way it does in Python. Treat it as eager
  validation plus cross-SDK env parity, and set `NODE_EXTRA_CA_CERTS` for
  the actual trust.

- **`proxyToken` — a `Proxy-Authorization` additional header.** Merged into
  the headers of **both** the REST client and the span exporter; an explicit
  `additionalHeaders['Proxy-Authorization']` entry wins over it.

```ts
initClient({
  publicKey: 'pk-...',
  secretKey: 'sk-...',
  baseUrl: 'https://langfuse.internal.example.com',
  cacertPath: '/etc/ssl/certs/corp-ca.pem',  // validated; exported via the OTel env-var rule
  proxyToken: 'Bearer ...',                  // if an auth proxy fronts Langfuse
});
```

## The certificate is deployment config

The PEM never ships inside the SDK — certificates rotate on IT's schedule,
differ per environment, and don't belong in a repo. Mount the bundle into
the container/host, point `NODE_EXTRA_CA_CERTS` at it in the start command
or Dockerfile `ENV`, and pass the same path as `cacertPath`.

Baking the corp CA into the image's *system* trust store
(`update-ca-certificates`) is **not** sufficient by itself on Node: unlike
Python's requests/httpx, Node uses its own bundled CA store and ignores the
system store unless launched with `--use-openssl-ca`. `NODE_EXTRA_CA_CERTS`
is usually the simpler, Node-native path.

## When no certificate is needed

The knobs are strictly opt-in. Local Docker (plain `http://langfuse:3000`
on a compose network), direct `cloud.langfuse.com`, or any endpoint with a
publicly-trusted certificate: omit `cacertPath`/`proxyToken`, don't set
`NODE_EXTRA_CA_CERTS`, and no custom TLS or env mutation happens at all.
