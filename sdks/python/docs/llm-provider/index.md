# llm-provider — getting started

## Construct one adapter, share it

```python
from llm_provider import LLMAdapter

adapter = LLMAdapter(
    api_key="sk-...",
    base_url="https://your-litellm-proxy.example.com/v1",
    timeout=600,               # seconds; must cover the WHOLE stream, not just TTFT
    reasoning_effort="medium", # forwarded when a request sets thinking=True
)
```

One adapter per `(api_key, base_url)` pair — typically instantiated once at
startup and injected wherever LLM access is needed. It is safe to share
across coroutines; the underlying `AsyncOpenAI` client handles concurrency.
`base_url` may point at any OpenAI-compatible endpoint, including a proxy
that rewrites model names or adds auth.

## The data model

```python
from llm_provider import ChatRequest, Message, SystemBlock

request = ChatRequest(
    model="claude-sonnet-5",
    messages=[
        Message(role="user", content="Summarise this deck."),
    ],
    max_output_tokens=16384,
)
```

| Type | Role |
|---|---|
| `Message` | One conversation turn: `role` (`user` / `assistant` / `tool`), optional `content`, `images` (base64 `ImageData`), `tool_calls`, `tool_call_id`. |
| `SystemBlock` | One segment of a structured system prompt; `cache=True` places a prompt-cache breakpoint after it. See [Prompt caching](caching.md). |
| `ChatRequest` | The request envelope: model, messages, tools, `thinking`, cache TTLs, `max_output_tokens`. |
| `StreamEvent` | Normalized streaming event (`event` name + `data` dict). See [Streaming & events](streaming.md). |

!!! warning "Always set `max_output_tokens` for long outputs"
    When `None`, the provider's often-low default applies and silently
    truncates large generations — classically a big tool call cut off
    mid-JSON. Set it to the model's real capacity for any caller that can
    emit large output.

## Choosing a call shape

| Method | Use when |
|---|---|
| `stream(request, system)` | The main agent loop: live tokens, thinking, tool calls. |
| `complete(request, system)` | You want tool/status events live but the text as one final `text` event (background jobs, tests). |
| `generate(request, system)` | Single-shot text: no streaming, no caching; tool calls and reasoning in the output are dropped. |
| `generate_chat_completion(...)` | Short utility calls (titles, labels, vision) — works where the Responses API is unavailable, supports cached system blocks. |
| `generate_image(...)` | Text-to-image via the Images API. |
| `list_models()` | Admin UIs / health checks; returns `[{"id", "owned_by"}]`. |

Details for the utility shapes are in
[Utility calls & images](utilities.md).

## Minimal streaming example

```python
async for event in adapter.stream(request, "You are a helpful analyst."):
    match event.event:
        case "text_delta":
            print(event.data["text"], end="")
        case "done":
            usage = event.data["usage"]
            print(f"\n{usage['input_tokens']} in / {usage['output_tokens']} out")
```

Errors never surface as `openai.*` exceptions — see
[Errors & retries](errors.md).

## Observability

When [tracing is initialised](../langfuse-client/index.md), every adapter
call wraps itself in a Langfuse observation — request input, output text,
and token usage **including the cache counters** (`cache_read_input_tokens`
/ `cache_creation_input_tokens`, so Langfuse's cache-aware pricing applies),
plus a `tool_calls` metadata summary (`call_id` + `name` per call) whenever
the turn used tools.

Attribute traces to a user and session by setting the identity fields —
they go to Langfuse only, **never to the LLM provider**:

```python
request = ChatRequest(
    model="claude-sonnet-5",
    messages=history,
    user_id="u-8842",             # per-user cost/usage aggregation
    session_id="conv-31",         # groups a conversation's traces
    trace_metadata={"app": "edwin"},
    trace_tags=["prod"],
)
```

`generate_chat_completion()` and `generate_image()` accept the same four as
keyword arguments. Identity propagates to the observation and every child
span, which is what Langfuse aggregations (cost per user, session
drill-down, tag filters) key on.

!!! note "`trace_metadata` takes any JSON — but keep it flat and short"
    The dict is schemaless, and non-string values are serialised to compact
    JSON automatically. Langfuse, however, propagates metadata as
    string values and **drops any value over 200 characters** (logged, not
    truncated). Use it for filterable dimensions — tenant, feature flag,
    experiment — not payloads. Full contract:
    [langfuse-client → the metadata contract](../langfuse-client/index.md#the-metadata-contract).

### Cost tracking

The SDK holds no pricing table — model names behind the proxy are
deployment-specific — so it defines an interface instead: pass a **pricer**
at construction and every generation gets a USD `cost_details` breakdown
alongside its usage.

```python
def price(model: str, usage: dict) -> dict | None:
    info = get_model_info(model)   # your app's pricing source
    if info is None:
        return None                # unknown model -> traced without cost
    return {
        "input": usage.get("input", 0) * info.input_cost_per_token,
        "output": usage.get("output", 0) * info.output_cost_per_token,
        "cache_read_input_tokens":
            usage.get("cache_read_input_tokens", 0) * info.cache_read_cost_per_token,
        "cache_creation_input_tokens":
            usage.get("cache_creation_input_tokens", 0) * info.cache_write_cost_per_token,
    }

adapter = LLMAdapter(api_key="sk-...", base_url="...", cost_fn=price)
```

The contract:

- The pricer receives the model id and the **exact `usage_details` dict
  reported on the trace**, so cost and usage can never disagree. Return a
  dict mirroring those keys (any subset); Langfuse sums them into the total
  unless you include an explicit `"total"`. An ingested cost always
  overrides anything Langfuse would infer from its own model definitions.
- Return `None` (or `{}`) for models you can't price — the call is traced
  without cost.
- Exceptions are caught and logged at `WARNING`; pricing can never break or
  slow-fail a call.
- Image generations additionally report an `images` custom usage type (the
  image count), so flat-priced models (DALL·E) can be billed per image —
  `usage["images"] * flat_rate` — while token-billed models (gpt-image-1)
  price from `input`/`output` as usual.

The zero-code alternative is defining the models and their prices in
Langfuse itself (Settings → Models, or `POST /api/public/models`): Langfuse
then infers cost server-side from the `usage_details` the SDK already
sends, including the cache counters. Note the proxy's model aliases won't
match Langfuse's built-in price list, so those definitions must be created
and kept in sync by hand — prefer `cost_fn` when the app already owns a
pricing source (e.g. LiteLLM's `/model/info`).

### Tool execution spans

Tool **execution** spans remain the application's job — the SDK streams the
model's tool calls but never runs them. Wrap your executor with the
[`span()` helper](../langfuse-client/index.md#tracing-your-own-code) inside
the streaming loop so execution nests under the same trace.
