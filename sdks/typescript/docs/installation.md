# Installation

Requires **Node >= 22**. Both packages are **ESM-only** (`"type": "module"`).

| Package | Import | Typical use |
| --- | --- | --- |
| `@genai-sdk/langfuse-client` | `@genai-sdk/langfuse-client` | Langfuse tracing only |
| `@genai-sdk/llm-provider` | `@genai-sdk/llm-provider` | LLM adapter (depends on langfuse-client) |

Published tarballs are on the org **JFrog Artifactory npm** feed after release tags
(`langfuse-client-v*` / `llm-provider-v*`) via
[`.github/workflows/typescript-sdks-release.yml`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/.github/workflows/typescript-sdks-release.yml).

---

## Install from JFrog in an application (recommended)

### 1. Get access

- **Registry URL** for scope `@genai-sdk`:  
  `https://artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/`
- **Credentials**: Artifactory identity token as `JFROG_TOKEN` (see [`.npmrc.example`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/typescript/.npmrc.example)).

Public dependencies (`openai`, `@langfuse/*`, …) still resolve from **npmjs**.

### 2. Project `.npmrc`

Copy the example into your app (or `~/.npmrc`):

```ini
@genai-sdk:registry=https://artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/
//artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/:_authToken=${JFROG_TOKEN}
//artifacts-central.pwc.com/artifactory/api/npm/npmdev-c0war-dvj-npm-loc/:always-auth=true
```

```bash
export JFROG_TOKEN="your-token"
pnpm add @genai-sdk/llm-provider@0.4.0
# tracing only:
pnpm add @genai-sdk/langfuse-client@0.5.0
```

### 3. Consumer guide

**[Using the SDK in your application](using-in-an-app.md)** — install, env vars, streaming, Langfuse, CI.

Optional local/Docker harness: [Test application](app-testing.md).

### 4. GitHub Actions (application repo)

```yaml
- uses: pnpm/action-setup@v4
- uses: actions/setup-node@v4
  with:
    node-version: 22
- run: cp sdks/typescript/.npmrc.example .npmrc
  env:
    JFROG_TOKEN: ${{ secrets.JFROG_TOKEN }}
- run: pnpm add @genai-sdk/llm-provider@0.4.0
```

---

## Install from git (monorepo / unreleased)

`@genai-sdk/llm-provider` depends on `@genai-sdk/langfuse-client` as `workspace:*`, which only resolves inside this repo. In an external pnpm project, use a git subdirectory dependency **plus an override**:

```jsonc
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

The `#path:` selector is **pnpm-specific**. npm/yarn cannot install from a git subdirectory; vendor `packages/*` into your workspace instead.

### Tracing-only from git

```jsonc
{
  "dependencies": {
    "@genai-sdk/langfuse-client": "github:pwc-me-adv-strategyand/infra-platform-services#path:sdks/typescript/packages/langfuse-client"
  }
}
```

---

## Development (this repository)

```bash
corepack enable
cd sdks/typescript
pnpm install
pnpm typecheck && pnpm test && pnpm build
```

Docs: `pnpm docs:build` (MkDocs under `docs/`).
