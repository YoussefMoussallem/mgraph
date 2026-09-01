# Deployment guide

## One code path, env-driven config

Write initialisation once; let the environment decide how much of it
activates:

```python
import os
from langfuse_client import init_client
from llm_provider import LLMAdapter

adapter = LLMAdapter(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ["LLM_BASE_URL"],
)

if os.getenv("LANGFUSE_ENABLED", "").lower() == "true":
    init_client(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ["LANGFUSE_BASE_URL"],
        cacert_path=os.getenv("LANGFUSE_CACERT_PATH") or None,
        proxy_token=os.getenv("LANGFUSE_PROXY_TOKEN") or None,
    )
```

Empty or missing env vars fall through to `None`, and `init_client` treats
empty strings as absent — a blank `LANGFUSE_CACERT_PATH=` line in a local
`.env` behaves identically to the variable not existing.

## The three tiers

| Tier | Langfuse | Certificates |
|---|---|---|
| **Local tests / CI** | Don't call `init_client()` — tracing is a free no-op. The SDK's own test suite runs this way: no credentials, no network. | None. |
| **Local Docker / direct cloud** | Keys + URL only. Self-hosted Langfuse on a compose network is plain `http://langfuse:3000` — no TLS to verify. | None. |
| **Hosted behind corporate proxy** | Keys + URL + `cacert_path` + `proxy_token`. | Mount the corp CA PEM into the container; pass its path. See [Corporate proxy / private CA](langfuse-client/corporate-network.md). |

## Process lifecycle

Register a shutdown hook so queued spans survive deploys and short-lived
runs (details in [Lifecycle & guarantees](langfuse-client/lifecycle.md)):

```python
from contextlib import asynccontextmanager
from langfuse_client import shutdown

@asynccontextmanager
async def lifespan(app):          # FastAPI example
    yield
    shutdown()                    # flush pending spans before exit
```

For gunicorn/uvicorn multi-worker setups, initialise in each worker's
startup hook — the client is per-process, and `init_client` is idempotent
and thread-safe within one.

## Timeouts

`LLMAdapter(timeout=...)` (default 600 s) applies per request and must cover
the **entire stream duration** — a long agent turn with tool use and
reasoning legitimately runs minutes. Use a second adapter with a short
timeout for latency-sensitive utility paths rather than lowering the main
one.

After release tags are published, installs use the org PyPI index — see [Installation](installation.md#install-from-jfrog-in-an-application-recommended).
