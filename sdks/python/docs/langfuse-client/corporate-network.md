# Corporate proxy / private CA

On a TLS-intercepting network (Zscaler, an internal gateway with a private
CA), Langfuse traffic fails certificate verification against the default
trust store. The fix must cover **two separate transports**, and missing the
second one fails *silently*:

1. **REST/ingestion API** — httpx-based; carries API calls and media.
2. **OTLP span exporter** — carries the actual traces. Requests-based,
   **ignores the httpx client entirely**, and takes its CA from the OTel
   env vars (`OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE`, falling back to
   `OTEL_EXPORTER_OTLP_CERTIFICATE`).

!!! danger "The silent half"
    Fix only the httpx side and everything *looks* healthy — API calls
    succeed, no errors are logged by your code — but traces never arrive in
    Langfuse. This split cost real debugging time in production; the SDK
    exists so no other app pays it again.

## One knob covers both

```python
init_client(
    public_key="pk-...",
    secret_key="sk-...",
    base_url="https://langfuse.internal.example.com",
    cacert_path="/etc/ssl/certs/corp-ca.pem",   # mounted into the container
    proxy_token="Bearer ...",                    # if an auth proxy fronts Langfuse
)
```

What happens internally:

- REST path: an `httpx.Client(verify=cacert_path)` is built with the
  `Proxy-Authorization` header attached.
- OTLP path: the token is merged into the exporter headers, and
  `OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE` is set — **only if the deployment
  configured neither OTel certificate variable**. The traces-scoped var
  outranks the general one, so platform-level OTel config always wins, and
  exporters for other signals (metrics, logs) are never touched.
- A caller-supplied `httpx_client` is used as-is (put the proxy header on
  it yourself); `cacert_path` then still covers the OTLP side.

## The certificate is deployment config

The PEM never ships inside the SDK — certificates rotate on IT's schedule,
differ per environment, and don't belong in a repo. Mount the bundle into
the container/host and pass its path.

An equally valid alternative for containerised deployments: bake the corp CA
into the image's system trust store (`update-ca-certificates` in the
Dockerfile, plus `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`). When the image trust
store already knows the CA, `cacert_path` isn't needed at all.

## When no certificate is needed

The knobs are strictly opt-in. Local Docker (plain `http://langfuse:3000`
on a compose network), direct `cloud.langfuse.com`, or any endpoint with a
publicly-trusted certificate: omit `cacert_path`/`proxy_token` and no custom
TLS or env mutation happens at all. See the
[Deployment guide](../deployment.md) for the env-driven pattern that serves
every tier with one code path.
