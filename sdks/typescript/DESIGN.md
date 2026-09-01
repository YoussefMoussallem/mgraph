# Design — genai-sdk TypeScript

TypeScript port of the sibling [Python SDK](../python/).
Same two-package split, same behavioral contracts, idiomatic TypeScript.
The behavioral contract mapping and the dual-SDK maintenance process live in
[PARITY.md](../PARITY.md).

## Goals

1. **Behavioral parity** with the Python SDK: same stream-event taxonomy, same
   chat-vs-responses routing rules, same prompt-caching semantics, same error
   hierarchy, same hardening guarantees (observation cleanup on abandonment,
   deferred `tool_call_start`, usage degradation, etc.).
2. **Idiomatic TypeScript** — not transliterated Python. Async generators for
   streams, options objects for constructors, `Error` subclasses, ESM.
3. **Zero runtime dependencies** beyond the platform SDKs (`openai`,
   `@langfuse/*`). Schemas are compile-time interfaces, not runtime validators —
   matching the Python SDK's choice of keeping `StreamEvent` validation-free in
   the hot loop.

## Repo layout

pnpm workspace, ESM-only, Node >= 22, TypeScript 5.x (strict, NodeNext),
built with plain `tsc`, tested with vitest 4.

| Package | npm name | Ports |
|---|---|---|
| `packages/llm-provider` | `@genai-sdk/llm-provider` | Python `llm_provider` |
| `packages/langfuse-client` | `@genai-sdk/langfuse-client` | Python `langfuse_client` |

`@genai-sdk/llm-provider` depends on `@genai-sdk/langfuse-client`
(`workspace:*`), exactly mirroring the Python dependency direction.
`langfuse-client` remains independently consumable for tracing-only apps.

NodeNext module resolution: relative imports in `src/` use explicit `.js`
extensions. Tests import package source via `../src/...js`; the cross-package
import resolves through a vitest alias so tests never require a prior build.

## Naming rules

- **API surface** (exported types, function/option/field names): camelCase.
  `max_output_tokens` → `maxOutputTokens`, `cache_ttl` → `cacheTtl`,
  `tool_call_id` → `toolCallId`, `mime_type` → `mimeType`, etc.
- **Stream-event contract** (event names AND `event.data` payload keys,
  including all usage objects): byte-for-byte identical to the Python SDK —
  `text_delta`, `tool_call_start`, `{"call_id": ...}`,
  `{"input_tokens": ..., "cache_read_input_tokens": ...}`. The normalized
  event stream is a cross-language wire contract: an app consuming events from
  either SDK sees identical shapes. Never camelCase these.
- Module filenames: camelCase (`chatCompletions.ts`), mirroring Python module
  names.

## Module map (Python → TypeScript)

| Python | TypeScript |
|---|---|
| `llm_provider/schemas.py` | `packages/llm-provider/src/schemas.ts` |
| `llm_provider/exceptions.py` | `packages/llm-provider/src/errors.ts` |
| `llm_provider/adapter/common.py` | `packages/llm-provider/src/adapter/common.ts` |
| `llm_provider/adapter/core.py` | `packages/llm-provider/src/adapter/core.ts` |
| `llm_provider/adapter/chat_completions.py` | `packages/llm-provider/src/adapter/chatCompletions.ts` |
| `llm_provider/adapter/responses.py` | `packages/llm-provider/src/adapter/responses.ts` |
| `llm_provider/adapter/images.py` | `packages/llm-provider/src/adapter/images.ts` |
| `llm_provider/mappers/chat_completions.py` | `packages/llm-provider/src/mappers/chatCompletions.ts` |
| `llm_provider/mappers/responses.py` | `packages/llm-provider/src/mappers/responses.ts` |
| `llm_provider/__init__.py` | `packages/llm-provider/src/index.ts` |
| `langfuse_client/client.py` | `packages/langfuse-client/src/client.ts` |
| `langfuse_client/tracing.py` | `packages/langfuse-client/src/tracing.ts` |
| `langfuse_client/__init__.py` | `packages/langfuse-client/src/index.ts` |

Tests map 1:1 as well: `tests/test_chat_stream.py` →
`packages/llm-provider/test/chatStream.test.ts`, and so on for every Python
test file. Every behavior a Python test pins, the TS twin pins.

## Key design decisions

### Schemas (`schemas.ts`)

Plain interfaces — `SystemBlock`, `ImageData`, `ToolCallData`, `Message`,
`ChatRequest` — plus the `StreamEvent` class (`{ event: string, data: ... }`)
and `systemText(blocks)`. Defaults that pydantic supplied (`cache: false`,
`cacheTtl: "5m"`, `thinking: false`) are applied at read sites via `??` (or a
small `normalizeRequest` helper), and the interface documents them. `Message.role`
is `'user' | 'assistant' | 'tool'`.

### Errors (`errors.ts`)

`ProviderError extends Error` carrying `statusCode: number | null`; subclasses
`ProviderAuthError`, `ProviderRateLimitError`, `ProviderNotFoundError`,
`ProviderInvalidRequestError`, `ProviderConnectionError`,
`ProviderServerError`; `classifyStatusError(statusCode, message)` with the
same mapping table (401/403, 429, 404, 400, >=500, fallback). Set
`this.name` and use `{ cause }` to chain the original SDK error.

### Error translation (`adapter/common.ts`)

Python's `translate_provider_errors()` context manager becomes
`toProviderError(err): ProviderError | null` (null → rethrow original) used in
`catch` blocks, or a `translateProviderErrors(fn)` async wrapper for
non-streaming calls. openai-node v6: `APIError` exposes `.status`;
**check `APIConnectionTimeoutError` before `APIConnectionError`** (subclass
relationship, same as Python) and map status errors through
`classifyStatusError`. Inside async generators, wrap the SDK iteration in
try/catch and rethrow translated — never leak `openai` error types.

### Streaming & cleanup

Each path (`chatCompletions.stream`, `responses.stream`) is an async generator
yielding `StreamEvent`s. `for await` consumers that `break`/`throw` trigger the
generator's `finally` blocks (JS calls `.return()`), which is where Langfuse
observations are ended — the same abandonment guarantee the Python SDK pins in
`test_adapter_hardening.py`. `LLMAdapter.stream()` returns the chosen path's
generator directly (no extra wrapper layer), so closing the outer iterator
closes the real one.

### LLMAdapter (`adapter/core.ts`)

```ts
new LLMAdapter({ apiKey, baseUrl, timeoutSeconds = 600, reasoningEffort = 'medium', client? })
```

`client?` is an injection point for tests (the Python tests monkeypatch
`adapter.client`; constructor injection is the TS-idiomatic equivalent).
Methods: `stream(request, systemPrompt)`, `complete(request, systemPrompt)`
(async generator: buffers `text_delta` into one `text` event, forwards other
events live, final `done` with full usage), `generate(request, systemPrompt?)`
→ `Promise<string>`, `generateChatCompletion({...})`, `generateImage({...})`,
`listModels()`. Routing rule identical to Python: cache-flagged system blocks
or `messageCacheTtl` → chat-completions path; everything else → responses
path. The runtime-learned `temperatureUnsupported: Set<string>` lives on the
adapter instance.

Return-shape adaptations (documented in PARITY.md): `generateChatCompletion`
returns `string`, or `{ text, usage }` when `returnUsage: true` (overloads);
`generateImage` returns `{ images: Buffer[], usage }`; `listModels` returns
`{ id, ownedBy }[]`.

### langfuse-client

Built on the current Langfuse JS SDK v5 (`@langfuse/tracing`,
`@langfuse/otel`, `@langfuse/client`), which is OTel-based like Langfuse
Python v3 — module-level state owned by this package, never read back from
Langfuse globals:

- `initClient({ publicKey, secretKey, baseUrl?, additionalHeaders?, cacertPath?, proxyToken? })`
  — idempotent per publicKey (different key → warn + keep existing), throws
  `Error` on empty credentials or nonexistent `cacertPath`. Registers a
  dedicated `NodeTracerProvider` with `LangfuseSpanProcessor`.
- `getClient()` — the `LangfuseClient` or `null`; never constructs implicitly.
- `generation(name, model, inputData?, attrs?)` / `span(...)` — return an
  observation handle (`update()`, `end(error?)`, plus `[Symbol.dispose]`) or
  `null` when tracing is off **or Langfuse throws** — tracing never takes
  down the traced call. Callers guard `if (obs)` and `end()` in `finally`;
  passing the propagating error to `end(error)` records it on the underlying
  OTel span (exception event + ERROR status) — the equivalent of the Python
  SDK exiting the Langfuse context manager with `sys.exc_info()`, so failed
  calls show up errored in Langfuse on both SDKs.
  Trace-level identity (userId, sessionId, metadata, tags) is applied via the
  v5 `propagateAttributes(params, fn)` API, wrapping the observation's
  creation so the span processor stamps the attributes at span start —
  best-effort, failures degrade to an un-attributed observation.
- `flush()` / `shutdown()` — never-throw; shutdown resets state so
  `getClient()` returns `null` again.
- Corporate-proxy support is ported to Node idioms: `proxyToken` becomes a
  `Proxy-Authorization` header on the span exporter; `cacertPath` is validated
  and honored as closely as the Node fetch stack allows (see
  `docs/langfuse-client/corporate-network.md` for the divergence notes vs
  Python's httpx/OTLP dual-transport story).

### Observability wrapping in llm-provider

Same shape as Python: each endpoint module opens a `generation`/`span`
observation when tracing is initialised, updates it with output + usage, and
**ends it in `finally`** — passing the propagating error (if any) to
`end(error)` so failed calls close as errored observations, while consumer
abandonment stays a clean close (Python: `GeneratorExit` is not recorded by
OTel's `use_span`). Failures updating observations are logged (via a minimal
internal logger on `console`), never thrown.

## Verification bar

`pnpm install && pnpm typecheck && pnpm test && pnpm build` must pass on
Node >= 22, Windows and Linux. Every ported test file green.
