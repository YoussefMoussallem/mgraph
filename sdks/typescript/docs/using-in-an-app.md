# Using the SDK in your application

End-to-end guide for **application repos** that consume **`@genai-sdk/*` from JFrog** (not the monorepo workspace). For a runnable reference, see [Test application](app-testing.md) (local gitignored harness + Docker).

Requires **Node.js >= 22**. Packages are **ESM-only** — use `"type": "module"` in `package.json` or `.mjs` entrypoints.

---

## 1. Install from JFrog

### Registry and credentials

| Item | Value |
| --- | --- |
| Scope | `@genai-sdk` |
| Registry | `https://artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/` |
| Auth | Artifactory identity token as `JFROG_TOKEN` (do not commit tokens) |

Copy [`.npmrc.example`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/typescript/.npmrc.example) into your app root (or `~/.npmrc`):

```ini
@genai-sdk:registry=https://artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/
//artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/:_authToken=${JFROG_TOKEN}
//artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/:always-auth=true
```

`openai`, `@langfuse/*`, and other public packages still resolve from **npmjs** — only `@genai-sdk/*` uses Artifactory.

### Dependencies

**LLM apps** (streaming, tools, caching):

```bash
export JFROG_TOKEN="your-token"
pnpm add @genai-sdk/llm-provider@0.4.0
```

`@genai-sdk/langfuse-client` is installed automatically as a dependency of `llm-provider`. If you call `initClient` / `flush` / `shutdown` in app code, add it explicitly so the dependency is obvious:

```bash
pnpm add @genai-sdk/langfuse-client@0.5.0 @genai-sdk/llm-provider@0.4.0
```

**Tracing-only apps** (no LLM adapter):

```bash
pnpm add @genai-sdk/langfuse-client@0.5.0
```

Example `package.json`:

```json
{
  "name": "my-genai-app",
  "type": "module",
  "engines": { "node": ">=22" },
  "dependencies": {
    "@genai-sdk/langfuse-client": "0.5.0",
    "@genai-sdk/llm-provider": "0.4.0"
  }
}
```

Pin versions to what your team has released; check Artifactory or GitHub releases after tag publishes.

### npm and yarn

Scoped registry in `.npmrc` works with **npm** and **yarn** as well as **pnpm**. Example with npm:

```bash
export JFROG_TOKEN="your-token"
npm install @genai-sdk/llm-provider@0.4.0
```

---

## 2. Configure the LLM proxy

Point the adapter at any **OpenAI-compatible** endpoint (e.g. internal LiteLLM / gateway):

| Variable | Required | Description |
| --- | --- | --- |
| `LLM_API_KEY` | yes | API key for the proxy |
| `LLM_BASE_URL` | yes | Base URL, usually ending in `/v1` |
| `LLM_MODEL` | no | Default model id for your deployment |

```ts
import { LLMAdapter } from '@genai-sdk/llm-provider';

const adapter = new LLMAdapter({
  apiKey: process.env.LLM_API_KEY!,
  baseUrl: process.env.LLM_BASE_URL!,
  // timeoutSeconds: 600,  // default; must cover full stream duration
});
```

---

## 3. Langfuse tracing (optional)

Tracing is a **no-op** until `initClient()` runs. Use env flags so the same code works in CI without credentials.

| Variable | When | Description |
| --- | --- | --- |
| `LANGFUSE_ENABLED` | optional | Set `true` to initialise tracing |
| `LANGFUSE_PUBLIC_KEY` | if enabled | Project public key |
| `LANGFUSE_SECRET_KEY` | if enabled | Project secret key |
| `LANGFUSE_BASE_URL` | if enabled | Cloud or self-hosted URL |
| `LANGFUSE_CACERT_PATH` | optional | Corporate CA PEM path |
| `LANGFUSE_PROXY_TOKEN` | optional | `Proxy-Authorization` for locked-down gateways |

```ts
import { flush, initClient, shutdown } from '@genai-sdk/langfuse-client';

function setupTracing(): void {
  if (process.env.LANGFUSE_ENABLED?.toLowerCase() !== 'true') {
    return;
  }
  initClient({
    publicKey: process.env.LANGFUSE_PUBLIC_KEY!,
    secretKey: process.env.LANGFUSE_SECRET_KEY!,
    baseUrl: process.env.LANGFUSE_BASE_URL!,
    cacertPath: process.env.LANGFUSE_CACERT_PATH || undefined,
    proxyToken: process.env.LANGFUSE_PROXY_TOKEN || undefined,
  });
}
```

On shutdown (CLI exit, server `SIGTERM`, Fastify `onClose`), flush pending spans:

```ts
await flush();
await shutdown();
```

Corporate TLS: [Corporate proxy / private CA](langfuse-client/corporate-network.md).

When tracing is on, `llm-provider` emits Langfuse observations named **`llm-stream`**, **`llm-generate`**, and **`llm-chat-completion`** for the matching adapter methods.

---

## 4. Call patterns

### Streaming chat

```ts
import type { ChatRequest } from '@genai-sdk/llm-provider';

const request: ChatRequest = {
  model: process.env.LLM_MODEL ?? 'your-model-id',
  messages: [{ role: 'user', content: 'Hello' }],
  maxOutputTokens: 8192,
};

for await (const event of adapter.stream(request, 'You are a helpful assistant.')) {
  if (event.event === 'text_delta') {
    process.stdout.write(event.data.text);
  } else if (event.event === 'done') {
    console.log('\nusage', event.data.usage);
  } else if (event.event === 'error') {
    throw new Error(String(event.data.message ?? 'stream error'));
  }
}
```

Treat unknown `event.event` values as no-ops — new event types may be added without a major bump. See [Streaming & events](llm-provider/streaming.md).

### One-shot generate

```ts
const text = await adapter.generate(request, 'You are helpful.');
```

### Utility / chat-completions path

Use when you need a short non-streaming string (titles, labels) or the Responses API is unavailable:

```ts
const result = await adapter.generateChatCompletion({
  model: process.env.LLM_MODEL ?? 'your-model-id',
  systemPrompt: 'Reply in one word.',
  userContent: 'Sky color on a clear day?',
  maxOutputTokens: 32,
  returnUsage: true,
});
console.log(result.text, result.usage);
```

Without `returnUsage: true`, the method returns a `string` only.

### Tool use and caching

- Tools: [Getting started — llm-provider](llm-provider/index.md)
- Prompt caching: [Prompt caching](llm-provider/caching.md)
- Errors: [Errors & retries](llm-provider/errors.md)

---

## 5. Server lifecycle (Fastify example)

```ts
import Fastify from 'fastify';
import { flush, shutdown } from '@genai-sdk/langfuse-client';

const app = Fastify();

app.addHook('onClose', async () => {
  await flush();
  await shutdown();
});

// setupTracing() once at startup
// register routes that use `adapter`
```

Initialise tracing **once per process**; `initClient` is idempotent per public key.

---

## 6. CI (GitHub Actions)

Store `JFROG_TOKEN` in secrets (same pattern as the SDK repo `jfrog` environment).

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
      - name: Auth to Artifactory npm
        run: cp path/to/.npmrc.example .npmrc
        env:
          JFROG_TOKEN: ${{ secrets.JFROG_TOKEN }}
      - run: pnpm install --frozen-lockfile
      - run: pnpm test
```

---

## 7. Docker

Build images with `JFROG_TOKEN` as a **build secret** (or build-arg in trusted pipelines), install with the same `.npmrc` scope, and pass `LLM_*` / `LANGFUSE_*` at runtime. A full stack with **mock LLM + self-hosted Langfuse** is documented in [Test application — Docker](app-testing.md#docker-mock-llm-langfuse-tracing).

---

## 8. Install without JFrog

For unreleased bits, use git subdirectory deps with **pnpm** overrides — see [Installation — from git](installation.md#install-from-git-monorepo-unreleased).

---

## Related

- [Installation](installation.md) — registry details and development clone
- [Deployment guide](deployment.md) — env-driven tiers (local / Docker / corporate)
- [PARITY.md](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/PARITY.md) — behavioral contract with the Python SDK
