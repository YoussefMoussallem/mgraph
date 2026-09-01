# PARITY — keeping the Python and TypeScript SDKs in sync

Two SDKs in this monorepo implement one behavioral contract:

- [`python/`](python/) — `llm-provider` + `langfuse-client` (pip)
- [`typescript/`](typescript/) — `@genai-sdk/llm-provider` + `@genai-sdk/langfuse-client` (pnpm)

The contract is **behavioral**, not line-by-line: same stream-event taxonomy,
same routing rules, same caching semantics, same error classification, same
hardening guarantees. Language idiom is free to differ; behavior is not.

## The shared contract

Identical across both SDKs (byte-for-byte where it's data):

1. **Stream events** — names (`text_delta`, `thinking_delta`,
   `tool_call_start`, `tool_call_delta`, `tool_call_done`, `web_search_*`,
   `error`, `done`, plus `text` from `complete()`) and every `data` payload
   key, including usage objects (`input_tokens`, `output_tokens`,
   `cache_read_input_tokens`, `cache_creation_input_tokens`). Unknown event
   names must be treated as no-ops by consumers in both ecosystems.
   The terminal `done` carries `stop_reason`: `end_turn` | `max_tokens` |
   `tool_use` | `error` | a verbatim provider reason | `null` (no terminal
   signal seen — completion unconfirmed). `response.incomplete` is a
   terminal: it surfaces the usage the call burned plus a truthful
   stop_reason, never a clean zero-usage `done`. Standalone `error` items
   in the Responses stream surface as `error` events.
2. **Routing** — an explicit per-request `transport` override
   (`chat` | `responses`) wins outright; otherwise cache-flagged system
   blocks or a message-cache TTL → Chat Completions path; otherwise →
   Responses path. Per-request `reasoning effort` overrides the adapter
   default.
3. **Caching semantics** — `cache: true` breakpoint blocks; `cache_control`
   only for the allowlisted model families (claude / anthropic / gemini /
   vertex); OpenAI models get plain text; `5m` default TTL, `1h` opt-in;
   message-cache breakpoint on the final message.
4. **Error hierarchy** — `ProviderError` + six subclasses with the same
   status-code mapping; underlying SDK exceptions never leak. A statusless
   SDK `APIError` (mid-stream SSE error payload) translates to
   `ProviderServerError`; a caller-initiated cancellation is deliberately
   NOT translated (the caller must be able to tell its own abort from a
   provider failure).
5. **Hardening guarantees** — everything `test_adapter_hardening.py` pins:
   observations closed on stream abandonment, deferred `tool_call_start`
   until id/name exist, paired start→done on trailing flush, usage-less
   terminal events degrade to zero counts, dropped hosted tools warn,
   `generate()` forwards the max-output-token cap.
6. **Langfuse lifecycle** — idempotent init keyed on public key
   (different key → warn + keep), get-or-null client access, never-throw
   `generation()`/`span()`/`flush()`/`shutdown()`.
7. **Langfuse cost tracking** — optional app-injected pricer on
   `LLMAdapter` (`cost_fn` / `costFn`) that maps `(model, usage_details)` →
   `cost_details` on every traced call shape; never throws; omitted when no
   pricer is configured. Image calls include an `images` usage dimension for
   flat-priced models.

## Deliberate divergences (TypeScript)

| Python | TypeScript | Why |
|---|---|---|
| snake_case API fields (`max_output_tokens`) | camelCase (`maxOutputTokens`) | language idiom; event payloads stay snake_case (wire contract) |
| `generate_chat_completion(..., return_usage=True)` → tuple | `{ text, usage }` object via overload | tuples are unidiomatic in TS |
| `generate_image` → `(list[bytes], usage)` | `{ images: Buffer[], usage }` | same |
| `timeout` (seconds) | `timeoutSeconds` | avoids seconds-vs-ms confusion in JS |
| `cost_fn` on `LLMAdapter` | `costFn` on `LLMAdapter` | same pricer contract; Langfuse field is `cost_details` / `costDetails` per SDK |
| tests monkeypatch `adapter.client` | constructor `client?:` injection | no monkeypatching culture in TS |
| `cacert_path` covers httpx + OTLP env var | `cacertPath` validated; Node trust story documented in `typescript/docs/langfuse-client/corporate-network.md` | Node's fetch/undici CA model differs from httpx |
| Langfuse Python v3 SDK | Langfuse JS SDK v5 (`@langfuse/*`) | current SDK line per ecosystem |
| `images.py` parses usage fields with bare `int()` (non-numeric string raises `ValueError`) | `images.ts` routes usage fields through `asInt`, degrading unparseable values to `0` | tolerance improvement: a malformed proxy usage payload must not fail an otherwise-successful image call |
| native asyncio task cancellation | `signal?: AbortSignal` on `ChatRequest` / utility options, surfacing as the SDK's untranslated `APIUserAbortError` | cancellation is a language-runtime concern: Python cancels the awaiting task; JS needs an explicit signal threaded to fetch |

## Test-file map

Every Python test file has a TS twin; a behavior pinned on one side must be
pinned on both:

| python | typescript |
|---|---|
| `python/llm_provider/tests/test_chat_stream.py` | `typescript/packages/llm-provider/test/chatStream.test.ts` |
| `python/llm_provider/tests/test_chat_mappers.py` | `typescript/packages/llm-provider/test/chatMappers.test.ts` |
| `python/llm_provider/tests/test_responses_stream.py` | `typescript/packages/llm-provider/test/responsesStream.test.ts` |
| `python/llm_provider/tests/test_responses_mappers.py` | `typescript/packages/llm-provider/test/responsesMappers.test.ts` |
| `python/llm_provider/tests/test_cache_blocks.py` | `typescript/packages/llm-provider/test/cacheBlocks.test.ts` |
| `python/llm_provider/tests/test_exceptions.py` | `typescript/packages/llm-provider/test/errors.test.ts` |
| `python/llm_provider/tests/test_utility_calls.py` | `typescript/packages/llm-provider/test/utilityCalls.test.ts` |
| `python/llm_provider/tests/test_observability.py` | `typescript/packages/llm-provider/test/observability.test.ts` |
| `python/llm_provider/tests/test_adapter_hardening.py` | `typescript/packages/llm-provider/test/adapterHardening.test.ts` |
| `python/langfuse_client/tests/test_init_client.py` | `typescript/packages/langfuse-client/test/initClient.test.ts` |
| `python/langfuse_client/tests/test_tracing.py` | `typescript/packages/langfuse-client/test/tracing.test.ts` |
| `python/llm_provider/tests/test_stream_terminals.py` *(pending port)* | `typescript/packages/llm-provider/test/streamTerminals.test.ts` |

## Maintenance process

1. **Spec first.** A behavior change starts as an edit to the contract above
   (or the docs both repos share structurally: caching, streaming, errors).
   If the change only makes sense in one language, it's a divergence — record
   it in the table, don't let it drift silently.
2. **Port with the test.** Implement in one SDK with its pinning test, then
   port code + test to the other before the change ships. The 1:1 test map is
   the sync mechanism — reviewing a PR here means checking the twin test
   exists there.
3. **Track provenance.** Each side's README lists hardening fixes not yet
   ported to the other (the Python repo already does this for edwin). Keep a
   short "pending parity" list in this file whenever the SDKs are
   temporarily out of sync.
4. **Version in lockstep on contract changes.** Cosmetic/language-local fixes
   version freely; any change to the shared contract bumps the same
   minor/major on both sides so app teams can reason about compatibility.

### Pending parity

Python must still port the TypeScript stream-terminal contract (see items
below) with `test_stream_terminals.py` as the designated twin, then bump to
the same minor as `@genai-sdk/llm-provider`:

- **`done.stop_reason`** — chat `finish_reason` normalisation
  (`stop`→`end_turn`, `length`→`max_tokens`, `tool_calls`/`function_call`→
  `tool_use`, unknown reasons verbatim, absent→`None`); Responses
  `response.completed` → `tool_use`/`end_turn`; forwarded through
  `complete()`'s outer `done`.
- **`response.incomplete` terminal** — usage + citations extracted, reason
  mapped (`max_output_tokens`→`max_tokens`, else verbatim, missing→
  `incomplete`). `response.failed` additionally sets `stop_reason: error`
  and extracts usage when present.
- **Standalone `error` stream items** → `error` events (previously dropped
  as unhandled).
- **Statusless `APIError` → `ProviderServerError`** in the error
  translator (mid-stream SSE error payloads must not leak SDK types).
- **Per-request `reasoning_effort`** overriding the adapter default.
- **Per-request `transport` override** (`chat` | `responses`) beating the
  caching-intent routing rule.
- **`generate_chat_completion` gains `max_output_tokens`** (forwarded as
  `max_tokens`) and **`temperature=None` omission** (send no temperature at
  all — the provider default applies — vs the unset-→0.3 default, which is
  unchanged).
- **`generate_image` gains `background`/`output_format` passthrough** (same
  provider-dialect contract as `quality`), **`n=None` omission** (backends
  that reject the param), and the result gains **`urls`** —
  URL-dialect items (a `url` with no `b64_json`) are surfaced for the caller
  to download instead of being silently dropped; the adapter still never
  fetches them itself.
- **Cancellation**: TS added `AbortSignal` plumbing (recorded as a
  divergence — Python's equivalent is asyncio cancellation, no port
  needed); Python should verify task cancellation mid-stream closes
  observations, which `test_adapter_hardening.py` already pins.
- **Removed** the TEMP `[web_search verify]` console.warn probe (Python
  side: remove its logger twin if present).
