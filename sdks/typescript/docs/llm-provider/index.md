# llm-provider — getting started

## Construct one adapter, share it

```ts
import { LLMAdapter } from '@genai-sdk/llm-provider';

const adapter = new LLMAdapter({
  apiKey: 'sk-...',
  baseUrl: 'https://your-litellm-proxy.example.com/v1',
  timeoutSeconds: 600,        // must cover the WHOLE stream, not just TTFT
  reasoningEffort: 'medium',  // forwarded when a request sets thinking: true
});
```

One adapter per `(apiKey, baseUrl)` pair — typically instantiated once at
startup and injected wherever LLM access is needed. It is safe to share
across concurrent callers; the underlying OpenAI client handles concurrency.
`baseUrl` may point at any OpenAI-compatible endpoint, including a proxy
that rewrites model names or adds auth. (`timeoutSeconds` is in seconds —
converted to the SDK's millisecond timeout internally. A `client` option
also exists as a test seam: pass a pre-built OpenAI client and no client is
constructed.)

## The data model

```ts
import type { ChatRequest } from '@genai-sdk/llm-provider';

const request: ChatRequest = {
  model: 'claude-sonnet-5',
  messages: [
    { role: 'user', content: 'Summarise this deck.' },
  ],
  maxOutputTokens: 16384,
};
```

| Type | Role |
|---|---|
| `Message` | One conversation turn: `role` (`'user'` / `'assistant'` / `'tool'`), optional `content`, `images` (base64 `ImageData`), `toolCalls`, `toolCallId`. |
| `SystemBlock` | One segment of a structured system prompt; `cache: true` places a prompt-cache breakpoint after it. See [Prompt caching](caching.md). |
| `ChatRequest` | The request envelope: model, messages, tools, `thinking`, cache TTLs, `maxOutputTokens`. |
| `StreamEvent` | Normalized streaming event (`event` name + `data` object — names and payload keys stay snake_case, a wire contract shared with the Python SDK). See [Streaming & events](streaming.md). |

> [!WARNING]
> **Always set `maxOutputTokens` for long outputs.** When unset, the
> provider's often-low default applies and silently truncates large
> generations — classically a big tool call cut off mid-JSON. Set it to the
> model's real capacity for any caller that can emit large output.

## Choosing a call shape

| Method | Use when |
|---|---|
| `stream(request, system)` | The main agent loop: live tokens, thinking, tool calls. |
| `complete(request, system)` | You want tool/status events live but the text as one final `text` event (background jobs, tests). |
| `generate(request, system?)` | Single-shot text: no streaming, no caching; tool calls and reasoning in the output are dropped. |
| `generateChatCompletion({...})` | Short utility calls (titles, labels, vision) — works where the Responses API is unavailable, supports cached system blocks. |
| `generateImage({...})` | Text-to-image via the Images API. |
| `listModels()` | Admin UIs / health checks; returns `[{ id, ownedBy }]`. |

Details for the utility shapes are in
[Utility calls & images](utilities.md).

## Minimal streaming example

```ts
for await (const event of adapter.stream(request, 'You are a helpful analyst.')) {
  switch (event.event) {
    case 'text_delta':
      process.stdout.write(event.data.text);
      break;
    case 'done': {
      const usage = event.data.usage;
      console.log(`\n${usage.input_tokens} in / ${usage.output_tokens} out`);
      break;
    }
  }
}
```

Errors never surface as `openai` SDK exceptions — see
[Errors & retries](errors.md).

## Observability

When [tracing is initialised](../langfuse-client/index.md), every adapter
call wraps itself in a Langfuse observation — request input, output text,
and token usage **including the cache counters** (reported to Langfuse as
`cache_read_input_tokens` / `cache_creation_input_tokens`, so Langfuse's
cache-aware pricing applies), plus a `tool_calls` metadata summary
(`call_id` + `name` per call) whenever the turn used tools.

Attribute traces to a user and session by setting the identity fields —
they go to Langfuse only, **never to the LLM provider**:

```ts
const request: ChatRequest = {
  model: 'claude-sonnet-5',
  messages: history,
  userId: 'u-8842',              // per-user cost/usage aggregation
  sessionId: 'conv-31',          // groups a conversation's traces
  traceMetadata: { app: 'edwin' },
  traceTags: ['prod'],
};
```

`generateChatCompletion()` and `generateImage()` accept the same four in
their options objects. Identity propagates to the observation and every
child span, which is what Langfuse aggregations (cost per user, session
drill-down, tag filters) key on.

Tool **execution** spans remain the application's job — the SDK streams the
model's tool calls but never runs them. Wrap your executor with the
[`span()` helper](../langfuse-client/index.md#tracing-your-own-code) inside
the streaming loop so execution nests under the same trace.
