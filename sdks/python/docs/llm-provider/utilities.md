# Utility calls & images

## `generate()` — single-shot text

```python
text = await adapter.generate(request, system_prompt="You are terse.")
```

Non-streaming Responses call that returns the assembled text. Use for prompt
refinement, summarisation — anywhere streaming adds complexity without user
value. Only `output_text` parts are concatenated; tool calls and reasoning
in the output are dropped (callers that need those use `stream()`).
Honours `max_output_tokens`. The Responses path never caches — cache flags
on the system prompt are ignored here.

## `generate_chat_completion()` — utility text & vision

```python
title = await adapter.generate_chat_completion(
    model="claude-haiku-4-5",
    system_prompt="Produce a 4-word chat title.",
    user_content="...first user message...",
)
```

Plain Chat Completions call for short utility generations — chat titles,
labels, classifications — and the fallback where the Responses API is
unavailable (some Azure OpenAI regions). No tools, no reasoning: system +
user only. Returns the content string; empty string means the model returned
no text (treat as failure).

**Vision** — `user_content` also accepts OpenAI content parts:

```python
description = await adapter.generate_chat_completion(
    model="claude-sonnet-5",
    system_prompt="Describe the image.",
    user_content=[
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ],
)
```

**Caching** — `system_prompt` accepts `SystemBlock`s; cache-flagged blocks
become breakpoints exactly as on the [streaming path](caching.md).

**Usage accounting** — pass `return_usage=True` to get
`(text, {"input_tokens", "output_tokens", "cache_read_tokens",
"cache_write_tokens"})` for cost bookkeeping.

!!! note "Temperature is self-healing"
    Some newer models hard-400 on the `temperature` parameter. The adapter
    catches that specific 400, drops the parameter, retries once, and
    remembers the model for the life of the process — one configured model
    id works across families without paying a failed round-trip every call.

## `generate_image()` — text-to-image

```python
images, usage = await adapter.generate_image(
    model="gpt-image-1",
    prompt="isometric city map, dawn light",
    size="1024x1024",
    quality="medium",   # only forwarded when set — accepted values differ by model
    n=1,
)
```

Returns `(images, usage)`: a list of raw PNG/JPEG byte strings and
`{"input_tokens", "output_tokens"}`. Bytes always come back inline
(`b64_json`) — no signed URL that can expire before you persist it. The
`response_format` parameter is auto-negotiated: models that reject it
(gpt-image-1) get a one-time retry without it.

Token-billed image models (gpt-image-1) report usage that scales with
size/quality, so a render can be priced from per-token rates like a text
turn; flat-priced models (DALL·E) report zeros — fall back to per-image
pricing.

## `list_models()`

```python
models = await adapter.list_models()   # [{"id": ..., "owned_by": ...}]
```

For admin UIs and health checks. Returns only the fields worth showing;
the SDK's full model record is noisy and version-dependent.
