# Streaming & events

`stream()` yields `StreamEvent` objects — a name plus a data dict — mapped
from whichever wire API the request took. Consumers never see raw SDK
events.

## Which wire API serves a request

| Request | Path |
|---|---|
| Cache-flagged `SystemBlock`s **or** `message_cache_ttl` set | **Chat Completions** — the only path where the LiteLLM proxy forwards `cache_control` breakpoints (see [Prompt caching](caching.md)). |
| Everything else (plain prompts, hosted `web_search_preview`, utilities) | **Responses API**. |

The two paths emit the **same normalized events** — consumers don't need to
know which one served them. One asymmetry: hosted tools (e.g.
`web_search_preview`) exist only on the Responses path; on the chat path
they are dropped with a warning (see [tools](#tools)).

## Event reference

| Event | Data | Notes |
|---|---|---|
| `text_delta` | `{text}` | One chunk of assistant text. |
| `thinking_delta` | `{text}` | Reasoning/summary text when `thinking=True`; raw reasoning and summaries surface identically. |
| `tool_call_start` | `{call_id, name}` | Emitted once per tool call, **only after the call's identity (id/name) is known** — `call_id` is never a placeholder that later events contradict. |
| `tool_call_delta` | `{call_id, delta}` | Raw JSON argument fragments, in order. Parse incrementally or wait for done. |
| `tool_call_done` | `{call_id, name, arguments}` | Complete argument string. Every started call gets exactly one; guaranteed even when the proxy omits `finish_reason`. |
| `web_search_start` / `web_search_searching` / `web_search_done` | `{}` | Hosted web-search progress (Responses path only). |
| `web_search_sources` | `{sources: [{url, title}]}` | Deduped `url_citation` annotations from the final response. |
| `error` | `{message}` | Provider reported a soft mid-stream failure; partial output before it is still valid. |
| `done` | `{usage: {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens}}` | Always the final event on success. |
| `text` | `{text}` | **`complete()` only**: the full concatenated text, emitted just before `done`. |

!!! tip "Treat unknown event names as no-ops"
    New event types can be added without a major version bump. A consumer
    that switches on known names and ignores the rest never breaks.

## Contracts you can rely on

- **Pairing** — every `tool_call_done` is preceded by exactly one
  `tool_call_start` with the same `call_id`, including degenerate streams
  where the provider never sent the call's identity.
- **Terminal usage** — `done` carries the request's token accounting,
  including cache reads/writes when the provider caches. If a proxy omits
  usage entirely, counts degrade to zero rather than erroring.
- **Abandonment-safe** — if a consumer stops iterating mid-stream (client
  disconnect), all internal resources including Langfuse observations are
  released deterministically.
- **Soft failures preserve output** — `error` is an event, not an
  exception, so partial text already streamed remains usable. Hard failures
  raise the [typed exceptions](errors.md) instead.

## Tools

Pass OpenAI-style function tool definitions on `ChatRequest.tools`:

```python
tools=[{
    "type": "function",
    "name": "get_weather",
    "description": "Get the weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                    "required": ["city"]},
}]
```

Tool-call arguments stream as raw JSON string fragments so UIs can render
progress before the call completes. Non-`function` tool types pass through
untouched on the Responses path; on the Chat Completions path they are
skipped with a logged warning (hosted tools don't exist there — keep hosted
web search on requests without caching intent).

## `complete()` — buffered text, live status

Wraps `stream()`: forwards tool/search/status events as they happen, but
collapses all `text_delta`s into a single `text` event before `done`. Use it
where character-by-character streaming adds complexity without value.
