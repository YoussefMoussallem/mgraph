# Prompt caching

Prompt caching cuts cost and latency by letting the provider reuse the
processed form of a stable prompt prefix across calls. In this SDK it is
**explicit and typed**: caching intent lives in the API surface, never in
hidden markers or side effects.

## The API

Pass the system prompt as `SystemBlock`s and flag each block that ends a
stable prefix:

```ts
import type { ChatRequest, SystemBlock } from '@genai-sdk/llm-provider';

const system: SystemBlock[] = [
  { text: STATIC_RULES, cache: true },   // breakpoint placed after this block
  { text: `Today is ${today}.` },        // volatile tail — uncached
];

const request: ChatRequest = {
  model: 'claude-sonnet-5',
  messages: history,
  cacheTtl: '5m',            // tier for the system breakpoints (default)
  messageCacheTtl: '5m',     // optional: also cache conversation history
};

for await (const event of adapter.stream(request, system)) {
  // ...
}
```

A plain `string` system prompt is equivalent to one unflagged block — **never
cached** — so non-caching callers keep the simplest possible call shape.
Multiple flagged blocks are allowed (e.g. one breakpoint after static rules,
another after a template appendix).

## How a request flows

1. **Normalization** — the system prompt becomes a list of non-empty blocks
   (`string` → one unflagged block; empty-text blocks are dropped, even when
   flagged).
2. **Routing** — cache-flagged blocks *or* `messageCacheTtl` route the
   request to Chat Completions, the only path where the LiteLLM proxy
   forwards `cache_control` to the backend. Everything else takes the
   Responses path as plain text.
3. **Rendering per model family** —
   Claude / Anthropic / Gemini / Vertex models (case-insensitive substring
   match on the model id) get content blocks with `cache_control` attached
   exactly where flagged. OpenAI models get plain joined text: they cache
   automatically by prefix and *reject* `cache_control` blocks. An unknown
   model family degrades to "no breakpoint" (suboptimal), never "rejected
   request" (broken).
4. **Accounting** — the `done` event reports `cache_read_tokens` /
   `cache_write_tokens`, extracted from OpenAI-style `cached_tokens` details
   or LiteLLM's Anthropic passthrough counters. That's how you verify
   caching actually hits.

## TTL tiers

| `cacheTtl` | Wire form | Works on |
|---|---|---|
| `'5m'` (default) | `{"type": "ephemeral"}` | Everything — Bedrock, Anthropic direct, Gemini via LiteLLM. |
| `'1h'` | `{"type": "ephemeral", "ttl": "1h"}` | Anthropic's extended-cache beta only. |

> [!CAUTION]
> **The Bedrock 1h trap.** Bedrock does not understand the explicit `ttl`
> field — it silently ignores the **entire** breakpoint, so nothing caches
> and nothing errors. On Bedrock-backed deployments keep the default `'5m'`.

## Conversation-history caching

`messageCacheTtl` attaches an additional breakpoint to the **last**
conversation message. Because provider caching is prefix-based, that single
breakpoint caches everything before it — system prefix *plus* full message
history. Next turn, that message sits mid-history: the request reads the
cached prefix and writes a fresh breakpoint at its own last message. The
breakpoint "moves" with the conversation, giving incremental history caching
across turns and across tool-use rounds within a turn. The system message
keeps its own (longer) tier — providers require longest-TTL-first ordering
when tiers mix.

## The invariant everything depends on

Prefix caching hits only when the bytes before a breakpoint are
**byte-identical across calls**. Any churn inside a flagged block — a
timestamp, unstable object-key or array ordering, a tool list that reorders —
turns every call into a silent cache miss. There is no error; you just pay
full price.

- Put volatile content in unflagged blocks *after* the last breakpoint.
- Keep flagged blocks deterministic: stable ordering, no timestamps.
- Watch `cache_read_tokens` in production — a hit rate of zero means the
  prefix is churning.
