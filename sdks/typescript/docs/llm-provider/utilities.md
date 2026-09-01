# Utility calls & images

## `generate()` — single-shot text

```ts
const text = await adapter.generate(request, 'You are terse.');
```

Non-streaming Responses call that resolves to the assembled text. Use for
prompt refinement, summarisation — anywhere streaming adds complexity
without user value. Only `output_text` parts are concatenated; tool calls
and reasoning in the output are dropped (callers that need those use
`stream()`). Honours `maxOutputTokens`. The Responses path never caches —
cache flags on the system prompt are ignored here. The system prompt
argument is optional (defaults to empty).

## `generateChatCompletion()` — utility text & vision

```ts
const title = await adapter.generateChatCompletion({
  model: 'claude-haiku-4-5',
  systemPrompt: 'Produce a 4-word chat title.',
  userContent: '...first user message...',
});
```

Plain Chat Completions call for short utility generations — chat titles,
labels, classifications — and the fallback where the Responses API is
unavailable (some Azure OpenAI regions). No tools, no reasoning: system +
user only. Resolves to the content string; empty string means the model
returned no text (treat as failure).

**Vision** — `userContent` also accepts OpenAI content parts:

```ts
const description = await adapter.generateChatCompletion({
  model: 'claude-sonnet-5',
  systemPrompt: 'Describe the image.',
  userContent: [
    { type: 'text', text: 'What is this?' },
    { type: 'image_url', image_url: { url: `data:image/png;base64,${b64}` } },
  ],
});
```

**Caching** — `systemPrompt` accepts `SystemBlock`s; cache-flagged blocks
become breakpoints exactly as on the [streaming path](caching.md). The
`cacheTtl` option defaults to `'1h'` here (unlike the streaming path's
`'5m'`) — utility prompts are stable, so the longer tier pays off; pass
`'5m'` explicitly on Bedrock-backed deployments (see
[the Bedrock 1h trap](caching.md#ttl-tiers)).

**Usage accounting** — pass `returnUsage: true` and the return type becomes
`{ text, usage }` (a typed overload), where `usage` carries the snake_case
wire keys `{ input_tokens, output_tokens, cache_read_tokens,
cache_write_tokens }` for cost bookkeeping:

```ts
const { text, usage } = await adapter.generateChatCompletion({
  model: 'claude-haiku-4-5',
  systemPrompt: 'Produce a 4-word chat title.',
  userContent: firstUserMessage,
  returnUsage: true,
});
```

> [!NOTE]
> **Temperature is self-healing.** Some newer models hard-400 on the
> `temperature` parameter (default `0.3`). The adapter catches that
> specific 400, drops the parameter, retries once, and remembers the model
> for the life of the adapter — one configured model id works across
> families without paying a failed round-trip every call.

## `generateImage()` — text-to-image

```ts
const { images, usage } = await adapter.generateImage({
  model: 'gpt-image-1',
  prompt: 'isometric city map, dawn light',
  size: '1024x1024',
  quality: 'medium',  // only forwarded when set — accepted values differ by model
  n: 1,
});
```

Resolves to `{ images, usage }`: `images` is an array of raw PNG/JPEG
`Buffer`s and `usage` is `{ input_tokens, output_tokens }`. Bytes always
come back inline (`b64_json`) — no signed URL that can expire before you
persist it. The `response_format` parameter is auto-negotiated: models that
reject it (gpt-image-1) get a one-time retry without it.

Token-billed image models (gpt-image-1) report usage that scales with
size/quality, so a render can be priced from per-token rates like a text
turn; flat-priced models (DALL·E) report zeros — fall back to per-image
pricing.

## `listModels()`

```ts
const models = await adapter.listModels();  // [{ id, ownedBy }]
```

For admin UIs and health checks. Returns only the fields worth showing
(camelCase `ownedBy`, per the [API naming rules](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/typescript/DESIGN.md#naming-rules));
the SDK's full model record is noisy and version-dependent. Note this is the
one adapter method without [error translation](errors.md) — raw SDK
exceptions propagate.
