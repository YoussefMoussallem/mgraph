# SDKs

Client libraries for consuming AI Labs shared services. Runtime service code lives under [`services/`](../services/), not here.

## Layout

```
sdks/
├── python/           # Python packages (one folder per installable SDK)
│   ├── langfuse-client/
│   ├── llm-provider/
│   └── m365/                   # Microsoft 365 family: one foundation, workloads on top
│       ├── m365-client/        # auth + Graph client foundation
│       ├── outlook-client/     # mail, calendar, contacts, on m365-client
│       ├── sharepoint-client/  # sites, libraries, files, lists, on m365-client
│       └── langchain-tools/    # the workloads as LangChain agent tools
└── typescript/       # TypeScript clients (pnpm workspace: @genai-sdk/*)
```

Each SDK folder has its own `README.md`, `pyproject.toml` (Python), and source under `src/`; the GenAI packages also carry their test suites. Packages that build on a shared foundation live together under a family folder ([`python/m365/`](python/m365/)) so the dependency direction is visible in the tree; they remain separately installable and versioned.

## Python packages

| Folder | PyPI name | Import | Version | Purpose |
| --- | --- | --- | --- | --- |
| [`python/langfuse-client/`](python/langfuse-client/) | `langfuse-client` | `langfuse_client` | 0.5.0 | Langfuse init + tracing helpers |
| [`python/llm-provider/`](python/llm-provider/) | `llm-provider` | `llm_provider` | 0.4.0 | OpenAI-compatible LLM adapter (streaming, tools, caching) |
| [`python/m365/m365-client/`](python/m365/m365-client/) | `m365-client` | `m365_client` | 0.1.0 | Microsoft 365 auth (on-behalf-of + app-only) and Graph client foundation |
| [`python/m365/outlook-client/`](python/m365/outlook-client/) | `outlook-client` | `outlook_client` | 0.1.0 | Outlook mail, attachments, calendar, contacts, profile (on `m365-client`) |
| [`python/m365/sharepoint-client/`](python/m365/sharepoint-client/) | `sharepoint-client` | `sharepoint_client` | 0.1.0 | SharePoint sites, document libraries, files, lists (on `m365-client`) |
| [`python/m365/langchain-tools/`](python/m365/langchain-tools/) | `m365-langchain-tools` | `m365_langchain_tools` | 0.1.0 | Outlook + SharePoint as 24 LangChain agent tools, writes opt-out (on the two workload SDKs) |

## Install

Published wheels are on **JFrog Artifactory PyPI**. For application repos, follow
[Python SDK installation](python/docs/installation.md) and [pip.jfrog.example](python/pip.jfrog.example).

Editable install from a clone of this repo (order matters — `llm-provider`
depends on `langfuse-client`):

```bash
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
```

`m365-client` is independent of the GenAI packages. The two Microsoft 365
workload packages depend on it, so install it in the same command:

```bash
pip install -e ./sdks/python/m365/m365-client \
  -e ./sdks/python/m365/outlook-client -e ./sdks/python/m365/sharepoint-client
```

Published wheels are built on **release tags** (Artifactory PyPI via `.github/workflows/python-sdks-release.yml`). Consumer install steps: [installation.md](python/docs/installation.md).

**Documentation site:** <https://pwc-me-adv-strategyand.github.io/infra-platform-services/> — MkDocs under `sdks/python/docs/` and `sdks/typescript/docs/` published as the `/python/` and `/typescript/` sub-sites alongside the platform docs (workflow `docs-site.yml`).

## TypeScript packages (GenAI)

| Package | npm name | Version | Purpose |
| --- | --- | --- | --- |
| [`typescript/packages/langfuse-client/`](typescript/packages/langfuse-client/) | `@genai-sdk/langfuse-client` | 0.5.0 | Langfuse init + tracing helpers |
| [`typescript/packages/llm-provider/`](typescript/packages/llm-provider/) | `@genai-sdk/llm-provider` | 0.4.0 | OpenAI-compatible LLM adapter (streaming, tools, caching, `costFn`) |

pnpm workspace root: [`typescript/`](typescript/). From a clone:

```bash
cd sdks/typescript && pnpm install && pnpm build && pnpm test
```

Published tarballs are built on **release tags** (Artifactory npm via `.github/workflows/typescript-sdks-release.yml`). Consumer test app (local, gitignored): [docs/app-testing.md](docs/app-testing.md). Cross-language contract: [`PARITY.md`](PARITY.md).

## Service mapping

| Service doc | SDKs |
| --- | --- |
| [SS-03 GenAI proxy](../docs/services/SS-03-genai.md) | `langfuse-client`, `llm-provider` (Python + TypeScript `@genai-sdk/*`) |
| — Outlook mailbox (delivered as an SDK, not a service) | `outlook-client` on `m365-client` (Python only) |
| — SharePoint sites & files (delivered as an SDK, not a service) | `sharepoint-client` on `m365-client` (Python only) |
| — Agent-host tool integration (Apex / LangChain) | `m365-langchain-tools` on the workload SDKs (Python only) |
| — future Teams integration | `m365-client` (Python only) |

## Versioning

- Bump version in each package’s `pyproject.toml` under `sdks/python/` (Python) or `package.json` under `sdks/typescript/packages/*` (TypeScript).
- Tag on **`main`** in this repo: `langfuse-client-v*` then `llm-provider-v*`; `m365-client-v*`, then `outlook-client-v*` / `sharepoint-client-v*`, then `m365-langchain-tools-v*` (each depends on the previous) — see `python-sdks-release` and `typescript-sdks-release` workflows.
- **Release-please shape differs by package.** The GenAI packages are dual-language, so their `release-please-config.json` entries key off the **TypeScript** package and use `extra-files` to sync the Python `pyproject.toml` version. `m365-client`, `outlook-client` and `sharepoint-client` are Python-only, so each has its own entry with `"release-type": "python"` pointing straight at its `sdks/python/m365/<name>` folder.
- The former standalone [genai-python-sdk](https://github.com/pwc-me-adv-strategyand/genai-python-sdk) repo is **deprecated** and will be removed.
