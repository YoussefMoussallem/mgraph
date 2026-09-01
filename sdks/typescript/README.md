# TypeScript SDK — GenAI (`sdks/typescript`)

TypeScript twin of the [Python SDK](../python/) in this monorepo:
a provider-agnostic LLM SDK. Two installable packages in a pnpm workspace:

| Package | Import | What it is |
|---|---|---|
| `@genai-sdk/llm-provider` | `packages/llm-provider` | Async LLM adapter built on the OpenAI Node SDK: streaming (Responses **and** Chat Completions), tool use, vision, image generation, prompt caching, normalized usage/cost accounting, and a provider-agnostic error hierarchy. |
| `@genai-sdk/langfuse-client` | `packages/langfuse-client` | Thin Langfuse initialisation + tracing helpers. `llm-provider` depends on it; tracing is a no-op until `initClient()` is called, so you can run without Langfuse credentials. |

Works against any OpenAI-compatible endpoint. Designed for a LiteLLM proxy
fronting Bedrock Claude, Azure OpenAI, and Gemini/Vertex — the same deployment
the Python SDK is production-tested behind.

Behavioral parity with the Python SDK is a maintained contract — see
[PARITY.md](../PARITY.md) for what is guaranteed identical, the deliberate
divergences, and the process for keeping the two SDKs in sync.
Architecture notes live in [DESIGN.md](DESIGN.md); per-package docs in
[`docs/`](docs/).

## Install

**Application teams:** [Using the SDK in your application](docs/using-in-an-app.md) (JFrog, env, streaming, Langfuse, CI).

Published to JFrog npm (`npmdev-c0war-dvj-npm-loc`) as **`@genai-sdk/*`**. ESM-only, Node >= 22.

### From Artifactory (apps / CI)

1. JFrog read access + identity token.
2. Copy [`.npmrc.example`](.npmrc.example) → `~/.npmrc` (or project `.npmrc`), set `JFROG_TOKEN` in the environment (do not commit the token).
3. Install:

```bash
pnpm add @genai-sdk/llm-provider
# tracing only:
pnpm add @genai-sdk/langfuse-client
```

`@langfuse/*`, `openai`, etc. still come from the public npm registry.

### From git (monorepo / unreleased)

Not on the registry yet for a given version — install from this repo:

```jsonc
// package.json
{
  "dependencies": {
    "@genai-sdk/llm-provider": "github:pwc-me-adv-strategyand/infra-platform-services#path:sdks/typescript/packages/llm-provider"
  },
  "pnpm": {
    "overrides": {
      "@genai-sdk/langfuse-client": "github:pwc-me-adv-strategyand/infra-platform-services#path:sdks/typescript/packages/langfuse-client"
    }
  }
}
```

Tracing-only apps take just `@genai-sdk/langfuse-client` (no `openai`
dependency comes along). In a monorepo you can instead vendor the two
`packages/*` directories into your workspace.

## Quickstart

```ts
import { ChatRequest, LLMAdapter } from '@genai-sdk/llm-provider';

const adapter = new LLMAdapter({
  apiKey: 'sk-...',
  baseUrl: 'https://your-litellm-proxy/v1', // any OpenAI-compatible endpoint
});

const request: ChatRequest = {
  model: 'claude-sonnet-5',
  messages: [{ role: 'user', content: 'Say hello.' }],
  maxOutputTokens: 8192,
};

// Streaming — normalized events: text_delta, thinking_delta, tool_call_start /
// tool_call_delta / tool_call_done, web_search_*, error, done (with usage).
for await (const event of adapter.stream(request, 'You are helpful.')) {
  if (event.event === 'text_delta') {
    process.stdout.write(event.data.text);
  } else if (event.event === 'done') {
    console.log('\n', event.data.usage);
  }
}

// Non-streaming utility call
const text = await adapter.generate(request, 'You are helpful.');
```

Treat unknown stream event names as no-ops — new event types can be added
without a major version bump. Event names and `data` payload keys (including
usage counts like `input_tokens` / `cache_read_input_tokens`) are identical to
the Python SDK's — the normalized event stream is a cross-language contract.

### Tool use

Pass OpenAI-style function tool definitions; tool-call arguments stream as raw
JSON fragments so partial input can be surfaced progressively:

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

Non-`function` tool types (e.g. `web_search_preview`) pass through to the
provider on the Responses path and are skipped (with a warning) on the Chat
Completions path.

### Prompt caching

Caching is explicit and typed: pass the system prompt as `SystemBlock`s and
flag each block that ends a stable prefix. A plain `string` system prompt is
never cached.

```ts
import type { SystemBlock } from '@genai-sdk/llm-provider';

const system: SystemBlock[] = [
  { text: STATIC_RULES, cache: true },   // cached prefix ends here
  { text: `Today is ${today}.` },        // volatile tail — uncached
];
for await (const event of adapter.stream(request, system)) {
  // ...
}
```

Requests with caching intent (cache-flagged blocks or `messageCacheTtl`)
stream over Chat Completions, where LiteLLM forwards the `cache_control`
breakpoints to Bedrock/Anthropic/Gemini; OpenAI models get plain text (they
cache automatically by prefix and reject `cache_control`); everything else
uses the Responses path. `cacheTtl` picks the tier: `"5m"` (default — the bare
form every backend accepts, including Bedrock) or `"1h"` (Anthropic's
extended-cache beta). `messageCacheTtl` additionally caches conversation
history via a breakpoint that moves to the last message each turn. Cache
reads/writes come back in the `done` event's usage counts.

Prefix caching only hits when the bytes before a breakpoint are identical
across calls — keep flagged blocks free of timestamps, unstable ordering, or
anything else that churns per request.

### Tracing (optional)

All calls are wrapped in Langfuse observations when the client is initialised;
otherwise tracing is a silent no-op:

```ts
import { initClient } from '@genai-sdk/langfuse-client';

initClient({ publicKey: 'pk-...', secretKey: 'sk-...', baseUrl: 'https://...' });
```

`initClient` is idempotent: calling it again with the same public key returns
the existing client, and a *different* key is ignored with a warning.
`getClient()` returns the client **or `null`** — it never constructs one
implicitly. `generation()` / `span()` return `null` when tracing is off *and*
when Langfuse itself throws — tracing can never take down the call being
traced. Always guard with `if (obs)`.

```ts
import { flush, shutdown } from '@genai-sdk/langfuse-client';

await flush();     // push pending spans now (they export in the background)
await shutdown();  // flush + terminal stop — call at process exit
```

`shutdown()` matters for short-lived processes (batch jobs, scripts, one-off
containers): spans still buffered in the exporter are lost on exit without it.
Behind a corporate proxy / private CA, see
[docs/langfuse-client/corporate-network.md](docs/langfuse-client/corporate-network.md).

### Errors

The adapter never leaks `openai` SDK exceptions. Catch the hierarchy exported
by `@genai-sdk/llm-provider`: `ProviderAuthError` (401/403),
`ProviderRateLimitError` (429), `ProviderNotFoundError` (404),
`ProviderInvalidRequestError` (400), `ProviderConnectionError`
(network/timeout), `ProviderServerError` (5xx) — all subclasses of
`ProviderError` carrying `statusCode` (and the original error as `cause`).
Retry policy is the caller's responsibility.

## Release (maintainers)

Same tags as Python: **`langfuse-client-v*`** / **`llm-provider-v*`** on `main`.

GitHub environment **`jfrog`** secrets (shared with Python PyPI publish):

| Secret | Used by |
|--------|---------|
| `JFROG_USERNAME` | PyPI (`twine`) |
| `JFROG_TOKEN` | PyPI + npm |
| `JFROG_NPM_URL` | npm publish |

PyPI also needs `JFROG_PYPI_URL` in the same environment (see Python README / `python-sdks-release.yml`).

Workflow: [`.github/workflows/typescript-sdks-release.yml`](../../.github/workflows/typescript-sdks-release.yml). Publish **langfuse-client** before **llm-provider** when releasing both.

Current package versions: `@genai-sdk/langfuse-client` **0.5.0**, `@genai-sdk/llm-provider` **0.4.0**.

### Consumer test app

Local harness under `sdks/typescript/app-testing/` is **gitignored** — see [Test application](docs/app-testing.md).

## Development

```bash
corepack pnpm install
pnpm typecheck && pnpm test && pnpm build
```

> [!IMPORTANT]
> **`dist/` is committed.** This monorepo is consumed as a git dependency
> (`github:...#path:`), and pnpm prepares git deps with npm, which cannot
> resolve the `workspace:*` link between the two packages — so there is no
> build-on-install; consumers get the committed `dist/` directly. Run
> `pnpm build` and include the `dist/` changes in any commit that touches
> `src/`.

> [!NOTE]
> **Windows + Node 24**: vitest 4.1.x fails at startup under Node 24.15 on
> Windows with `ERR_PACKAGE_IMPORT_NOT_DEFINED` for `#module-evaluator` —
> a Node ESM-resolver interaction with pnpm's symlinked `.pnpm` layout, not
> an SDK problem. Workaround until a fixed vitest/Node lands: install with
> the hoisted layout just for local runs —
> `npm_config_node_linker=hoisted corepack pnpm install --force` — after
> which the suite runs normally. CI (Linux, Node 22) is unaffected.

## Provenance

Ported 2026-07-24 from the sibling [Python SDK](../python/) (itself extracted from the
edwin monorepo). The port includes all of the Python SDK's hardening
guarantees, pinned by `packages/llm-provider/test/adapterHardening.test.ts`.
