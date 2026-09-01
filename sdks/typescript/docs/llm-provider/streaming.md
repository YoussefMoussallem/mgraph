# Streaming & events

`stream()` returns an async generator of `StreamEvent` objects — a name plus
a data object — mapped from whichever wire API the request took. Consumers
never see raw SDK events. Event names and every `data` payload key stay
**snake_case**: the normalized event stream is a wire contract shared
byte-for-byte with the Python SDK.

## Which wire API serves a request

| Request | Path |
|---|---|
| `transport: "chat"` or `transport: "responses"` set | That path, unconditionally — the explicit override wins. |
| Cache-flagged `SystemBlock`s **or** `messageCacheTtl` set | **Chat Completions** — the only path where the LiteLLM proxy forwards `cache_control` breakpoints (see [Prompt caching](caching.md)). |
| Everything else (plain prompts, hosted `web_search_preview`, utilities) | **Responses API**. |

The two paths emit the **same normalized events** — consumers don't need to
know which one served them. One asymmetry: hosted tools (e.g.
`web_search_preview`) exist only on the Responses path; on the chat path
they are dropped with a warning (see [tools](#tools)). Forcing
`transport: "responses"` on a cache-flagged request means the cache flags
are **not** realised (the system prompt rides as plain-text
`instructions`).

## Event reference

| Event | Data | Notes |
|---|---|---|
| `text_delta` | `{text}` | One chunk of assistant text. |
| `thinking_delta` | `{text}` | Reasoning/summary text when `thinking: true`; raw reasoning and summaries surface identically. |
| `tool_call_start` | `{call_id, name}` | Emitted once per tool call, **only after the call's identity (id/name) is known** — `call_id` is never a placeholder that later events contradict. |
| `tool_call_delta` | `{call_id, delta}` | Raw JSON argument fragments, in order. Parse incrementally or wait for done. |
| `tool_call_done` | `{call_id, name, arguments}` | Complete argument string. Every started call gets exactly one; guaranteed even when the proxy omits `finish_reason`. |
| `web_search_start` / `web_search_searching` / `web_search_done` | `{}` | Hosted web-search progress (Responses path only). |
| `web_search_sources` | `{sources: [{url, title}]}` | Deduped `url_citation` annotations from the final response. |
| `error` | `{message}` | Provider reported a soft mid-stream failure (`response.failed` or a standalone error item); partial output before it is still valid. |
| `done` | `{usage: {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens}, stop_reason}` | Always the final event on success. `stop_reason`: `"end_turn"`, `"max_tokens"` (truncated by the output cap — usage still reflects what was burned), `"tool_use"`, `"error"` (an `error` event preceded this), a verbatim provider reason (e.g. `"content_filter"`), or `null` when no terminal signal arrived — treat `null` as completion **unconfirmed**, not success. |
| `text` | `{text}` | **`complete()` only**: the full concatenated text, emitted just before `done` (which keeps the inner stream's `stop_reason`). |

> [!TIP]
> **Treat unknown event names as no-ops.** New event types can be added
> without a major version bump. A consumer that switches on known names and
> ignores the rest never breaks.

## Contracts you can rely on

- **Pairing** — every `tool_call_done` is preceded by exactly one
  `tool_call_start` with the same `call_id`, including degenerate streams
  where the provider never sent the call's identity.
- **Terminal usage** — `done` carries the request's token accounting,
  including cache reads/writes when the provider caches. If a proxy omits
  usage entirely, counts degrade to zero rather than erroring.
- **Abandonment-safe** — if a consumer stops iterating mid-stream (a
  `break` or `throw` inside `for await`, or a client disconnect), JavaScript
  calls the generator's `return()`, its `finally` blocks run, and all
  internal resources including Langfuse observations are released
  deterministically. If you drive the iterator manually with `.next()`
  instead of `for await`, call `.return()` yourself when abandoning it.
- **Soft failures preserve output** — `error` is an event, not an
  exception, so partial text already streamed remains usable. Hard failures
  reject with the [typed errors](errors.md) instead.
- **Truncation is visible and billed** — `response.incomplete` is handled
  as a terminal: `done` reports `stop_reason: "max_tokens"` (or the
  provider's reason) **and** the usage the call actually burned. A
  truncated stream never masquerades as a clean zero-usage completion.
- **Cancellation** — pass an `AbortSignal` as `ChatRequest.signal` to
  cancel the underlying HTTP request, before the first byte or mid-stream.
  The abort surfaces as the openai SDK's `APIUserAbortError` (deliberately
  untranslated — see [errors](errors.md)); abandoning the iterator without
  a signal still tears everything down, it just can't cancel a request
  that hasn't produced its first event yet.

## Tools

Pass OpenAI-style function tool definitions on `ChatRequest.tools`:

```ts
const request: ChatRequest = {
  model: 'claude-sonnet-5',
  messages: history,
  tools: [{
    type: 'function',
    name: 'get_weather',
    description: 'Get the weather for a city',
    parameters: {
      type: 'object',
      properties: { city: { type: 'string' } },
      required: ['city'],
    },
  }],
};
```

Tool-call arguments stream as raw JSON string fragments so UIs can render
progress before the call completes. Non-`function` tool types pass through
untouched on the Responses path; on the Chat Completions path they are
skipped with a logged warning (hosted tools don't exist there — keep hosted
web search on requests without caching intent).

## `complete()` — buffered text, live status

Wraps `stream()`: forwards tool/search/status events as they happen, but
collapses all `text_delta`s into a single `text` event before `done` (whose
`usage` is the full object from the inner stream, so cache counters survive
to the caller). Use it where character-by-character streaming adds
complexity without value.
