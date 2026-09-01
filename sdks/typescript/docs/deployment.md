# Deployment guide

How to run application code that depends on **`@genai-sdk/*`** across environments. Package install is always from JFrog (or git) — see [Using the SDK in your application](using-in-an-app.md).

## One code path, env-driven config

```ts
import { flush, initClient, shutdown } from '@genai-sdk/langfuse-client';
import { LLMAdapter } from '@genai-sdk/llm-provider';

const adapter = new LLMAdapter({
  apiKey: process.env.LLM_API_KEY!,
  baseUrl: process.env.LLM_BASE_URL!,
});

if (process.env.LANGFUSE_ENABLED?.toLowerCase() === 'true') {
  initClient({
    publicKey: process.env.LANGFUSE_PUBLIC_KEY!,
    secretKey: process.env.LANGFUSE_SECRET_KEY!,
    baseUrl: process.env.LANGFUSE_BASE_URL!,
    cacertPath: process.env.LANGFUSE_CACERT_PATH || undefined,
    proxyToken: process.env.LANGFUSE_PROXY_TOKEN || undefined,
  });
}

// on shutdown:
await flush();
await shutdown();
```

Empty env vars should be treated as unset; `initClient` rejects empty keys.

## Tiers

| Tier | Langfuse | LLM endpoint | Notes |
| --- | --- | --- | --- |
| **Unit tests / CI** | Do not call `initClient()` | Mock or skip LLM calls | Tracing is a free no-op |
| **Local / Docker** | `LANGFUSE_ENABLED=true` + keys + `http://langfuse-web:3000` on compose network | `http://mock-llm:8080/v1` or real proxy URL | See [Test application](app-testing.md) |
| **Hosted / corporate** | Keys + URL + optional `cacertPath` / `proxyToken` | Internal gateway (`LLM_BASE_URL`) | [Corporate proxy / private CA](langfuse-client/corporate-network.md) |

## Process lifecycle

- Call **`flush()`** and **`shutdown()`** before process exit so OTLP spans are delivered.
- In multi-worker Node deployments, initialise tracing in **each worker**; `initClient` is idempotent per `publicKey` within a process.
- Details: [Lifecycle & guarantees](langfuse-client/lifecycle.md).

## Timeouts

`LLMAdapter` default timeout is **600 seconds** per request and applies to the **full stream**. Long agent turns with tools need that headroom; use a separate adapter instance with a shorter timeout for quick utility calls rather than lowering the main one.

## Package feed

Only install steps and `.npmrc` / CI secrets change between environments — not application TypeScript. See [Using the SDK in your application](using-in-an-app.md).
