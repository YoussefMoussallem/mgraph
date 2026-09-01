# langfuse-client — getting started

Langfuse initialisation, lifecycle, and tracing helpers, built on the
Langfuse JS SDK v5 (`@langfuse/client`, `@langfuse/tracing`,
`@langfuse/otel`). The design goal: **apps run identically with tracing on
or off** — no credentials leaking into test/dev environments, and no way for
observability to break the code being observed.

## Initialise once at startup

```ts
import { initClient } from '@genai-sdk/langfuse-client';

const client = initClient({
  publicKey: 'pk-...',
  secretKey: 'sk-...',
  baseUrl: 'https://cloud.langfuse.com',
});
```

| Option | Purpose |
|---|---|
| `publicKey` / `secretKey` | Langfuse project credentials. Empty values throw an `Error` immediately. |
| `baseUrl` | Langfuse instance (cloud, EU, self-hosted). Defaults to `https://cloud.langfuse.com`; trailing slashes are stripped. |
| `cacertPath` | Private CA bundle path — validated eagerly, exported per the OTel env-var rule. See [Corporate proxy / private CA](corporate-network.md) for the Node trust story. Must exist on disk or `Error`. |
| `proxyToken` | `Proxy-Authorization` header value for an upstream proxy, applied to both the REST client and the span exporter. |
| `additionalHeaders` | Extra headers for API + span-export requests; an explicit `Proxy-Authorization` entry wins over `proxyToken`. |

Apps that never call `initClient()` simply run without tracing — every
helper is a silent no-op.

> [!NOTE]
> **Init success ≠ valid credentials.** Langfuse validates credentials
> lazily in the background. A successful `initClient` proves configuration
> shape, not authorization — watch the logs on first export.

Unlike the Python SDK there is no bring-your-own-HTTP-client escape hatch
(Python's `httpx_client`): the JS SDK rides Node's built-in fetch, so
transport customisation happens via `additionalHeaders` and process-level
knobs like `NODE_EXTRA_CA_CERTS`
([corporate-network.md](corporate-network.md)).

## Tracing LLM calls — automatic via llm-provider

Every `LLMAdapter` call wraps itself in a Langfuse observation when tracing
is initialised — input, output, token usage (including cache counters), and
a tool-call summary. Attribute traces to users and sessions via the
request's identity fields (`userId`, `sessionId`, `traceMetadata`,
`traceTags`) — see
[llm-provider observability](../llm-provider/index.md#observability).

## Tracing your own code

```ts
import { generation, span } from '@genai-sdk/langfuse-client';

const obs = generation(
  'chat.title',
  'claude-haiku-4-5',
  { chars: 512 },
  {
    userId: 'u-8842',      // optional trace identity — propagated to the
    sessionId: 'conv-31',  // observation and every child span
  },
);
if (obs) {                 // null when tracing is off — always guard
  try {
    const result = doTheCall();
    obs.update({ output: result });
  } finally {
    obs.end();             // exactly once, on every path
  }
}
```

The handle also implements `Symbol.dispose`, so with TypeScript 5.2+ you can
let `using` call `end()` for you (`using obs = generation(...)` — a `null`
handle is skipped automatically).

Both helpers take an optional fourth `TraceAttrs` argument — `userId`,
`sessionId`, `metadata`, `tags` — the attributes Langfuse aggregations (cost
per user, session drill-down, tag filters) key on. A failure while setting
them degrades to an un-attributed observation — never a broken call.

- `generation(name, model, inputData?, attrs?)` — for LLM calls; the
  generation observation type carries the model field Langfuse uses for
  token/cost dashboards.
- `span(name, model, inputData?, attrs?)` — for non-LLM work (tool
  execution, retrieval, orchestration). The span type has no model field, so
  `model` is folded into the input payload.

Both return `null` when tracing is off **and** when Langfuse itself fails —
see [Lifecycle & guarantees](lifecycle.md) for the full contract.

## Direct access

```ts
import { getClient } from '@genai-sdk/langfuse-client';

const lf = getClient();  // the initialised LangfuseClient, or null — never constructs one
```

Use for the full Langfuse SDK surface (scoring, datasets, media). Unlike
letting the upstream SDK self-construct, this never implicitly creates a
client — see [Lifecycle & guarantees](lifecycle.md) for why that matters.
