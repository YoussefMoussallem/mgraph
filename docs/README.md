# AI Labs platform documentation

Architecture standards, service contracts, application scaffolds, and SDK documentation for AI Labs shared services. These pages are published to the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/) on every merge to `main`; the source of truth is the [`infra-platform-services`](https://github.com/pwc-me-adv-strategyand/infra-platform-services) repository.

## Architecture

| Document | Description |
| --- | --- |
| [foundation-services-and-sharing-framework.md](architecture/foundation-services-and-sharing-framework.md) | Full engineering standard — principles, topology, RLS, APIs, adoption checklist |

## Per-service docs

| Service | Doc |
| --- | --- |
| SS-01 Identity & SSO | [SS-01-identity.md](services/SS-01-identity.md) |
| SS-02 User Directory | [SS-02-directory.md](services/SS-02-directory.md) |
| SS-03 GenAI proxy | [SS-03-genai.md](services/SS-03-genai.md) |
| Universal Sharing Framework | [SS-sharing-framework.md](services/SS-sharing-framework.md) |
| SS-05 Knowledge Base / RAG | [SS-05-knowledge-base.md](services/SS-05-knowledge-base.md) |
| SS-12 File / Blob Storage | [SS-12-storage.md](services/SS-12-storage.md) |

Outlook and SharePoint are delivered as Python SDKs rather than services — [`outlook-client`](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/outlook-client/) and [`sharepoint-client`](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/sharepoint-client/) on the Python SDK sub-site.

## Scaffolds

Application starter templates. These pages are single-sourced from the scaffold READMEs under [`scaffolds/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/scaffolds) — one page per scaffold:

| Scaffold | Doc |
| --- | --- |
| Overview & catalog | [scaffolds/index.md](scaffolds/index.md) |
| Frontend — React + Vite + Entra ID auth | [scaffolds/frontend.md](scaffolds/frontend.md) |
| Backend — FastAPI + Entra ID auth | [scaffolds/backend.md](scaffolds/backend.md) |

## SDKs

Python (GenAI and Microsoft 365) and TypeScript GenAI SDK documentation is built from `sdks/*/docs/` and published as sub-sites:

- [Python SDKs](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/)
- [TypeScript GenAI SDKs](https://pwc-me-adv-strategyand.github.io/infra-platform-services/typescript/)

Catalog, install instructions, and versioning policy: [`sdks/README.md`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/README.md).

## Contributing

Repo-wide rules for humans and AI agents: [contributing.md](contributing.md) (single-sourced from the root `CONTRIBUTING.md`).

## Diagrams

Platform-specific architecture diagrams may live in consuming repos (e.g. `vector410/docs/`). Canonical contracts and service behavior are defined here.
