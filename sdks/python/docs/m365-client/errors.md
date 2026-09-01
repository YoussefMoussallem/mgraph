# Errors & retries

Consuming services catch against one taxonomy instead of importing Kiota's
`ODataError` or Azure's `ClientAuthenticationError`, so nothing outside this
package branches on integer status codes.

## Hierarchy

```
M365Error
├── M365ConfigError               invalid settings — fails at boot, never retry
├── M365AuthError                 token acquisition failed; Graph never reached
│                                 carries aadsts_code + correlation_id
└── GraphError                    Graph returned an error
    ├── GraphAuthError            401/403 — permission not granted; do not retry
    ├── GraphThrottledError       429 — carries retry_after
    ├── GraphNotFoundError        404 — do not retry
    ├── GraphInvalidRequestError  400 — fix the input
    ├── GraphConflictError        409/412 — ETag conflict; re-read and retry
    └── GraphServerError          5xx — retry with backoff
```

Every error carries `status_code` where one exists. `GraphError` adds
`graph_code` (Graph's own machine-readable code, finer-grained than the status
and worth logging) and `request_id` (quote it in support tickets).

## Two auth errors, deliberately unrelated

| | Meaning | Usual cause |
|---|---|---|
| `M365AuthError` | Entra refused to issue a token. **Graph was never reached.** | Missing admin consent, unexposed API scope, wrong secret, expired certificate, unusable assertion. |
| `GraphAuthError` | Graph rejected a token we *did* obtain. | The permission was never granted or consented — not that the user lacks access. |

`M365AuthError` is not a subclass of `GraphError`. A handler that conflates the
two cannot tell a configuration problem from a permissions problem, which are
fixed in different places by different people.

`M365AuthError` surfaces `aadsts_code` and `correlation_id` verbatim when Entra
provides them. These are exactly what Microsoft support asks for; dropping them
turns a five-minute diagnosis into an afternoon of guessing.

```python
except M365AuthError as exc:
    log.error("token exchange failed: %s [%s / %s]",
              exc, exc.aadsts_code, exc.correlation_id)
```

No code-to-remediation table is baked in. Hardcoding `AADSTS` numbers from
memory produces confidently wrong hints, which are worse than none — build that
mapping from codes actually observed in your tenant.

## Why `GraphConflictError` exists

A 412 `If-Match` precondition failure means someone modified the item since you
read it. In SharePoint and Outlook that is **routine rather than exceptional**,
so it gets its own catchable type: the correct response is to re-read the item
and retry the write, not to fail the request.

## `translate_graph_errors()` is the calling convention

```python
async with translate_graph_errors():
    messages = await client.me.messages.get()
```

Not an optional nicety. Because the package hands back the official client and
steps out of the call path, it has **no interception point** — translation
cannot be middleware. Skip the context manager and every consumer ends up
catching `ODataError` and branching on status codes at each call site, which is
the coupling the taxonomy exists to prevent.

`translate_graph_errors_sync()` is the synchronous twin, for management scripts
and migrations. Prefer the async form in service code.

Unrecognised exceptions pass through untouched — your own `ValueError` stays a
`ValueError`. Cancellation is never translated: `asyncio.CancelledError` derives
from `BaseException`, so it bypasses the handler entirely and a caller can
always distinguish its own abort from a Graph failure.

## Retries happen before you see an error

Graph's own middleware retries 429 and 503 honouring `Retry-After`, bounded by
`max_retries` (default 3). This is the strongest single argument for the
official SDK: Graph throttles aggressively and per-workload, and a hand-rolled
retry loop that ignores `Retry-After` gets throttled harder.

So a `GraphThrottledError` reaching your code means the **retry budget is
already spent**:

```python
except GraphThrottledError as exc:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
```

Back off well beyond `retry_after` and reduce concurrency rather than retrying
harder.

### Throttling is not always a 429

Graph sometimes signals it as a **503 carrying a throttle code**
(`activityLimitReached`, `quotaLimitReached`, `serviceNotAvailable`,
`requestThrottled`). Classifying that as a plain `GraphServerError` would lose
the `retry_after` the caller needs, so the Graph error code outranks the
transport status for these four. Other codes never override the status.

## Statusless failures

A connection failure, DNS problem, or timeout produces an error with no
response. These map to `GraphServerError` with `status_code=None` so callers
retry, rather than to a 4xx that reads as permanent.

## A backstop handler

Route handlers should translate the cases they can act on. Add one handler for
the rest so an SDK error never escapes as an untyped 500:

```python
@app.exception_handler(M365Error)
async def m365_error_handler(request, exc: M365Error) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code if exc.status_code and exc.status_code >= 400 else 502,
        content={"code": type(exc).__name__, "detail": str(exc)},
    )
```

`{"code", "detail"}` is the platform error envelope from the API design rules.
