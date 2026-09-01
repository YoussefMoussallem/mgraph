# Errors & retries

The adapter never leaks `openai` SDK exceptions — every SDK error is
translated at the boundary into a typed hierarchy, so application code never
imports the OpenAI SDK or branches on integer status codes. (The two
documented exceptions: `listModels()`, and caller-initiated aborts — see
below.)

## Hierarchy

```
ProviderError                      base — extends Error, carries .statusCode
├── ProviderAuthError              401/403 — bad or missing API key
├── ProviderRateLimitError         429     — rate limit / quota exceeded
├── ProviderNotFoundError          404     — unknown or retired model id
├── ProviderInvalidRequestError    400     — malformed input / unsupported param
├── ProviderConnectionError        network failure or timeout
└── ProviderServerError            5xx     — provider-side failure
```

Every error carries `statusCode: number | null` and chains the original SDK
error via the standard ES2022 `cause` property. All classes (plus the
`classifyStatusError(statusCode, message, cause?)` mapping helper) are
exported from the package root.

## Retry guidance

| Error | Retry? | Typical cause |
|---|---|---|
| `ProviderAuthError` | No — alert operators | Config error: wrong key, expired credential. |
| `ProviderRateLimitError` | Yes, with backoff | Quota or concurrency limit. |
| `ProviderNotFoundError` | No | Wrong model id, model retired, proxy route missing. |
| `ProviderInvalidRequestError` | No — fix the request | Bad schema, oversized input, unsupported parameter. |
| `ProviderConnectionError` | Yes, with backoff | Network blip, proxy restart, stream timeout. |
| `ProviderServerError` | Yes, with backoff; alert if persistent | Provider incident. |

```ts
import {
  ProviderConnectionError,
  ProviderRateLimitError,
  ProviderServerError,
} from '@genai-sdk/llm-provider';

try {
  for await (const event of adapter.stream(request, system)) {
    // ...
  }
} catch (err) {
  if (
    err instanceof ProviderRateLimitError ||
    err instanceof ProviderConnectionError ||
    err instanceof ProviderServerError
  ) {
    // transient — schedule a retry with backoff
  } else {
    throw err;
  }
}
```

## Boundary semantics worth knowing

- **Retry policy is the caller's job.** The SDK classifies; it deliberately
  does not retry (except two narrow parameter-negotiation retries:
  `temperature` and image `response_format`, each once — see
  [Utility calls & images](utilities.md)). Put backoff loops in the
  application layer where request-level context lives.
- **Timeouts are connection errors.** A request exceeding the adapter's
  `timeoutSeconds` rejects with `ProviderConnectionError('Request timed
  out')`. Remember the timeout must cover the entire stream duration, not
  time-to-first-token.
- **Soft mid-stream failures don't throw.** If the provider reports a
  failure after output started, the stream yields an `error` event and
  preserves the partial output; thrown errors are for failures where no
  usable response exists.
- **Statusless SDK errors still translate.** An `APIError` with no HTTP
  status — the shape the SDK raises for a mid-stream SSE `{"error": ...}`
  payload after the HTTP 200 (a proxy/upstream failure) — becomes
  `ProviderServerError`, keeping it inside the hierarchy and classified as
  retryable. The never-leak guarantee covers mid-stream failures too.
- **Your own abort is not a provider failure.** Cancelling via
  `ChatRequest.signal` (or the `signal` option on utility calls) rejects
  with the openai SDK's `APIUserAbortError`, deliberately **untranslated**
  so callers can tell their own cancellation apart from an outage — never
  retry it.
- **Tracing can't mask errors.** Langfuse observations are closed on every
  exit path (success, error, consumer abandonment) before the error
  propagates.
- **`listModels()` is the one untranslated method.** A parity quirk
  preserved from the Python SDK: it applies no error translation, so raw
  SDK exceptions propagate from it. Every other adapter method holds the
  never-leak guarantee.
