# langfuse-client — getting started

Langfuse initialisation, lifecycle, and tracing helpers. The design goal:
**apps run identically with tracing on or off** — no credentials, no
Langfuse install requirements leaking into test/dev environments, and no
way for observability to break the code being observed.

## Initialise once at startup

```python
from langfuse_client import init_client

client = init_client(
    public_key="pk-...",
    secret_key="sk-...",
    base_url="https://cloud.langfuse.com",
)
```

| Parameter | Purpose |
|---|---|
| `public_key` / `secret_key` | Langfuse project credentials. Empty values raise `ValueError` immediately. |
| `base_url` | Langfuse instance (cloud, EU, self-hosted). Trailing slashes are stripped. |
| `cacert_path` | Private CA bundle covering **both** transports — see [Corporate proxy / private CA](corporate-network.md). Must exist on disk or `ValueError`. |
| `proxy_token` | `Proxy-Authorization` header value for an upstream proxy, applied to both transports. |
| `httpx_client` | Escape hatch: bring your own httpx client for the REST path (used as-is). |
| `additional_headers` | Extra headers for API + OTLP requests; explicit entries win over `proxy_token`. |

Apps that never call `init_client()` simply run without tracing — every
helper is a silent no-op.

!!! note "Init success ≠ valid credentials"
    Langfuse validates credentials lazily in the background. A successful
    `init_client` proves configuration shape, not authorization — watch the
    logs on first export.

## Tracing LLM calls — automatic via llm-provider

Every `LLMAdapter` call wraps itself in a Langfuse observation when tracing
is initialised — input, output, token usage (including cache counters), and
a tool-call summary. Attribute traces to users and sessions via the
request's identity fields (`user_id`, `session_id`, `trace_metadata`,
`trace_tags`) — see
[llm-provider observability](../llm-provider/index.md#observability).

## Tracing your own code

```python
from langfuse_client import generation, span

ctx = generation(
    "chat.title",
    model="claude-haiku-4-5",
    input_data={"chars": 512},
    user_id="u-8842",        # optional trace identity — propagated to the
    session_id="conv-31",    # observation and every child span
)
if ctx:                       # None when tracing is off — always guard
    with ctx as obs:
        result = do_the_call()
        obs.update(output=result)
```

Both helpers accept `user_id`, `session_id`, `metadata`, and `tags`; the
attributes are what Langfuse aggregations (cost per user, session
drill-down, tag filters) key on. A failure while setting them degrades to
an un-attributed observation — never a broken call.

### The `metadata` contract

`metadata` takes **any JSON object** — no schema — but it is stamped onto
*every span in the trace*, and Langfuse constrains that propagation path:

- Keys must be US-ASCII strings (non-string keys are coerced with `str()`).
- String values pass through untouched. Non-string values are serialised by
  the SDK as **compact JSON** (`{"flags": {"beta": True}}` arrives as
  `'{"beta":true}'`, `3` as `"3"`) — without this, Langfuse's own `str()`
  coercion would store Python reprs (`"{'beta': True}"`).
- Values whose final string exceeds **200 characters are dropped** by
  Langfuse with a logged warning — not truncated, gone.

So the rule of thumb: metadata is for **flat, short, filterable
dimensions** — tenant, feature flag, experiment variant, correlation id.
Large or deeply nested payloads don't belong here; put them in
`input_data`, in the observation's `update(metadata=...)` (stored verbatim
as nested JSON, but not propagated to child spans), or leave them out of
tracing entirely.

- `generation(name, model, input_data)` — for LLM calls; the generation
  observation type carries the model field Langfuse uses for token/cost
  dashboards.
- `span(name, model, input_data)` — for non-LLM work (tool execution,
  retrieval, orchestration). The span type has no model field, so `model`
  is folded into the input payload.

Both return `None` when tracing is off **and** when Langfuse itself fails —
see [Lifecycle & guarantees](lifecycle.md) for the full contract.

## Direct access

```python
from langfuse_client import get_client

lf = get_client()   # the initialised client, or None — never constructs one
```

Use for the full Langfuse SDK surface (scoring, datasets, media). Unlike
upstream `langfuse.get_client()`, this never implicitly constructs a client
— see [Lifecycle & guarantees](lifecycle.md) for why that matters.
