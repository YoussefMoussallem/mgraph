# Errors & retries

The adapter never leaks `openai.*` exceptions — every SDK error is
translated at the boundary into a typed hierarchy, so application code never
imports the OpenAI SDK or branches on integer status codes.

## Hierarchy

```
ProviderError                      base — carries .status_code
├── ProviderAuthError              401/403 — bad or missing API key
├── ProviderRateLimitError         429     — rate limit / quota exceeded
├── ProviderNotFoundError          404     — unknown or retired model id
├── ProviderInvalidRequestError    400     — malformed input / unsupported param
├── ProviderConnectionError        network failure or timeout
└── ProviderServerError            5xx     — provider-side failure
```

## Retry guidance

| Exception | Retry? | Typical cause |
|---|---|---|
| `ProviderAuthError` | No — alert operators | Config error: wrong key, expired credential. |
| `ProviderRateLimitError` | Yes, with backoff | Quota or concurrency limit. |
| `ProviderNotFoundError` | No | Wrong model id, model retired, proxy route missing. |
| `ProviderInvalidRequestError` | No — fix the request | Bad schema, oversized input, unsupported parameter. |
| `ProviderConnectionError` | Yes, with backoff | Network blip, proxy restart, stream timeout. |
| `ProviderServerError` | Yes, with backoff; alert if persistent | Provider incident. |

```python
from llm_provider.exceptions import (
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderServerError,
)

try:
    async for event in adapter.stream(request, system):
        ...
except (ProviderRateLimitError, ProviderConnectionError, ProviderServerError):
    ...  # transient — schedule a retry with backoff
```

## Boundary semantics worth knowing

- **Retry policy is the caller's job.** The SDK classifies; it deliberately
  does not retry (except two narrow parameter-negotiation retries:
  `temperature` and image `response_format`, each once). Put backoff loops
  in the application layer where request-level context lives.
- **Timeouts are connection errors.** A request exceeding the adapter's
  `timeout` raises `ProviderConnectionError("Request timed out")`. Remember
  the timeout must cover the entire stream duration, not time-to-first-token.
- **Soft mid-stream failures don't raise.** If the provider reports a
  failure after output started, the stream yields an `error` event and
  preserves the partial output; exceptions are for failures where no usable
  response exists.
- **Tracing can't mask errors.** Langfuse observations are closed on every
  exit path (success, error, consumer abandonment) before the exception
  propagates.
