# Test application (consumer reference)

Optional **local** harness at `sdks/typescript/app-testing/` (**gitignored** — create on your machine). It installs **`@genai-sdk/*` from JFrog** outside the monorepo workspace and runs stream / generate / chat-completion with optional Langfuse.

**Primary consumer guide (copy into your app):** [Using the SDK in your application](using-in-an-app.md).

---

## Docker (mock LLM + Langfuse tracing)

Validates published npm packages end-to-end without a real LLM proxy.

**Prerequisites:** `JFROG_TOKEN` in the environment or in `app-testing/.env`.

```powershell
cd sdks/typescript/app-testing
copy .npmrc.example .npmrc
copy .env.example .env

docker compose -p ts-genai-sdk -f docker-compose.yml -f docker-compose.langfuse.yml up -d
docker compose -p ts-genai-sdk -f docker-compose.yml -f docker-compose.langfuse.yml run --rm sdk-test
```

| Item | Value |
| --- | --- |
| **Langfuse UI** | http://localhost:3001 (TypeScript stack; Python `app-testing` uses **3000**) |
| **Login** | `dev@app-testing.local` / `devpassword` |
| **Project keys** | `pk-lf-app-testing` / `sk-lf-app-testing` (set on `sdk-test` by compose overlay) |
| **Worker** | `docker compose up -d` must include **`langfuse-worker`** or traces may not appear in the UI |

Expected traces after `sdk-test`: **`llm-stream`**, **`llm-generate`**, **`llm-chat-completion`** (Node / OpenTelemetry metadata).

Tear down:

```powershell
docker compose -p ts-genai-sdk -f docker-compose.yml -f docker-compose.langfuse.yml down
```

---

## Local Node (no Docker)

1. Create `app-testing/` and copy [`.npmrc.example`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/typescript/.npmrc.example) → `.npmrc`.
2. `package.json` with `@genai-sdk/llm-provider@0.4.0` and `@genai-sdk/langfuse-client@0.5.0` if you import tracing APIs.
3. `.env` with `LLM_*` and optional `LANGFUSE_*` (see [Using the SDK in your application](using-in-an-app.md)).

```bash
export JFROG_TOKEN="your-token"
pnpm install
pnpm all   # or: stream | generate | chat-completion
```

---

## Troubleshooting

- **No traces in UI** — use Langfuse on **port 3001** for this stack; ensure **`langfuse-worker`** is running; confirm `LANGFUSE_ENABLED=true` on `sdk-test`.
- **`ERR_PNPM_FETCH_*` for `@genai-sdk/*`** — `.npmrc` scope registry or expired `JFROG_TOKEN`.
- **401/403 from LLM** — VPN, proxy, or API key for real endpoints (Docker uses mock LLM by default).
