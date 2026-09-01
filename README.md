# AI Labs Shared Services

Foundation services and contracts for AI Labs platforms (Agent Studio, Edwin, and future apps).

Each service is **independently deployed** and **multi-tenant**. Platforms consume them via HTTP APIs — never by embedding service code.

## How to contribute

**Full coding rules and best practices → [`CONTRIBUTING.md`](CONTRIBUTING.md).** Read it before opening a PR or letting an AI agent write code in this repo.

**Published docs site → <https://pwc-me-adv-strategyand.github.io/infra-platform-services/>** — architecture, service contracts, scaffold guides, and SDK docs, rebuilt on every merge to `main` (see [`.github/workflows/docs-site.yml`](.github/workflows/docs-site.yml)).

This repo is organized into six areas. Keep each in its lane and update the right README when you add or change something.

### 1. `docs/` — planning and overall architecture

Use this for cross-cutting standards: system context, design principles, data placement, API contracts, and adoption guides.

- **Architecture docs** live under [`docs/architecture/`](docs/architecture/) (e.g. the foundation services standard).
- **Per-service reference docs** live under [`docs/services/`](docs/services/) (one markdown file per service, e.g. `SS-01-identity.md`).
- Update [`docs/README.md`](docs/README.md) when you add a new architecture or service doc.

### 2. `sdks/` — client libraries and SDK documentation

Use this for language-specific client libraries and their docs — not service runtime code.

- Each SDK gets its own folder under the language directory, e.g. `sdks/python/<sdk-name>/` or `sdks/typescript/<sdk-name>/`. Packages that build on a shared foundation live together under a family folder — the Microsoft 365 SDKs are `sdks/python/m365/<sdk-name>/`.
- Every SDK folder must have its own `README.md` (installation, usage, API reference).
- Update [`sdks/README.md`](sdks/README.md) when you add a new SDK or language.
- Link the SDK from the matching service doc (`docs/services/`) and from the root README below.

### 3. `services/` — service implementations and service docs

Use this for independently deployable microservices.

- Each service gets its own folder under [`services/`](services/), e.g. `services/identity/`.
- Every service folder must have its own `README.md` (status, stack, local dev, endpoints).
- Add or update the matching doc in [`docs/services/`](docs/services/).
- Update the **Service catalog** table in this README when you add a new service.

### 4. `scaffolds/` — application starter templates

Use this for templates that new AI Labs apps are **copied** from (not installed as dependencies).

- Each scaffold gets its own folder under [`scaffolds/`](scaffolds/), e.g. `scaffolds/frontend/`.
- Every scaffold folder must have its own `README.md` (quickstart, architecture, dependency rationale).
- Update the **Catalog** table in [`scaffolds/README.md`](scaffolds/README.md) when you add a new scaffold.

### 5. `apps/` — reference applications

Use this for complete, runnable applications built on the SDKs and scaffolds — proof that the pieces fit together end to end.

- Each app gets its own folder under [`apps/`](apps/), e.g. `apps/m365-workspace/`, with `backend/` and/or `frontend/`.
- Every app folder must have its own `README.md` (what it does, the registration and permissions it needs, how to run it).
- Apps consume SDKs as dependencies, never by copying SDK code; anything generic goes back into the scaffold or SDK.
- Update the catalog in [`apps/README.md`](apps/README.md) and add a job to `.github/workflows/apps-ci.yml` when you add one.

### 6. Root `README.md` — repo summary (this file)

This is the entry point. Keep it current as the repo grows.

When you add a **service**, **SDK**, **scaffold**, or **app**, update:

- The **Service catalog** table (for services)
- The **SDKs** section (for client libraries)
- The **Scaffolds** section (for starter templates)
- The **Apps** section (for reference applications)
- Any cross-links so readers can jump from summary → service folder → service doc → SDK doc

### Checklist when adding something new

| You add… | Also update… |
| --- | --- |
| New service | `services/<name>/README.md`, `docs/services/SS-XX-<name>.md`, `docs/README.md`, the `Services` nav in `mkdocs.yml`, service catalog in this README, and — if it has tests — a test job in `.github/workflows/services-ci.yml` |
| New SDK | `sdks/<lang>/<name>/README.md` (under the family folder if it has one, e.g. `sdks/python/m365/`), `sdks/README.md`, SDK section in this README, SDK section in the related `docs/services/` file |
| New scaffold | `scaffolds/<name>/README.md`, catalog in `scaffolds/README.md`, Scaffolds section in this README |
| New app | `apps/<name>/README.md`, catalog in `apps/README.md`, Apps section in this README, a job in `.github/workflows/apps-ci.yml` |
| Architecture change | `docs/architecture/`, then align affected `docs/services/` files |

**Convention:** one folder per service/SDK/scaffold, one README per folder, one service doc per service under `docs/services/`. If it is not documented in the right README, it is not done.

## Repository layout

```
infra-platform-services/
├── docs/           # Architecture standards and per-service documentation
├── services/       # Service implementations (one folder per service)
├── sdks/           # Client libraries (GenAI SDKs + Microsoft 365 foundation and workload SDKs)
├── scaffolds/      # Application starter templates (copied to start a new app)
└── apps/           # Reference applications built on the SDKs and scaffolds
```

## Service catalog

| ID | Service | Folder | Docs |
| --- | --- | --- | --- |
| SS-01 | Identity & SSO | [`services/identity/`](services/identity/) | [`docs/services/SS-01-identity.md`](docs/services/SS-01-identity.md) |
| SS-02 | User Directory | [`services/directory/`](services/directory/) | [`docs/services/SS-02-directory.md`](docs/services/SS-02-directory.md) |
| SS-03 | GenAI proxy | [`services/genai/`](services/genai/) | [`docs/services/SS-03-genai.md`](docs/services/SS-03-genai.md) |
| — | Universal Sharing Framework | [`services/sharing/`](services/sharing/) | [`docs/services/SS-sharing-framework.md`](docs/services/SS-sharing-framework.md) |
| SS-05 | Knowledge Base / RAG | [`services/knowledge-base/`](services/knowledge-base/) | [`docs/services/SS-05-knowledge-base.md`](docs/services/SS-05-knowledge-base.md) |
| SS-12 | File / Blob Storage | [`services/storage/`](services/storage/) | [`docs/services/SS-12-storage.md`](docs/services/SS-12-storage.md) |

Outlook and SharePoint are **not** services. They ship as SDKs — [`outlook-client`](sdks/python/m365/outlook-client/) and [`sharepoint-client`](sdks/python/m365/sharepoint-client/) on top of [`m365-client`](sdks/python/m365/m365-client/) — that an app embeds to call Microsoft Graph as the signed-in user. There is no proxy to deploy, and nothing an app reads through them is anything the caller could not read themselves.

## Architecture

The full engineering standard — design principles, data placement, RLS templates, API contracts, and adoption checklist — lives in:

**[docs/architecture/foundation-services-and-sharing-framework.md](docs/architecture/foundation-services-and-sharing-framework.md)**

## SDKs

Client libraries live under [`sdks/`](sdks/) — GenAI proxy and observability in both languages, plus the Microsoft 365 foundation and workload SDKs in Python:

| Language | Packages | Docs |
| --- | --- | --- |
| Python | [`langfuse-client`](sdks/python/langfuse-client/), [`llm-provider`](sdks/python/llm-provider/), [`m365-client`](sdks/python/m365/m365-client/), [`outlook-client`](sdks/python/m365/outlook-client/), [`sharepoint-client`](sdks/python/m365/sharepoint-client/), [`m365-langchain-tools`](sdks/python/m365/langchain-tools/) | [published](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/) · [source](sdks/python/docs/) |
| TypeScript | `@genai-sdk/langfuse-client`, `@genai-sdk/llm-provider` in [`sdks/typescript/`](sdks/typescript/) | [published](https://pwc-me-adv-strategyand.github.io/infra-platform-services/typescript/) · [sdks/typescript/README.md](sdks/typescript/README.md) · [sdks/PARITY.md](sdks/PARITY.md) |

Overview and install instructions: [`sdks/README.md`](sdks/README.md).

## Scaffolds

Starter templates new AI Labs apps are copied from live under [`scaffolds/`](scaffolds/):

| Scaffold | Folder | What you get |
| --- | --- | --- |
| Frontend | [`scaffolds/frontend/`](scaffolds/frontend/) | React + Vite SPA with Microsoft Entra ID auth wired end to end (login, route guard, silent refresh, authenticated fetch). Based on Edwin's client. |
| Backend | [`scaffolds/backend/`](scaffolds/backend/) | FastAPI service with Entra ID JWT validation, platform error envelope, request-id tracing, and optional Microsoft Graph on-behalf-of wiring (`app/graph.py`) on the M365 SDKs. Based on Edwin's backend. |

Published scaffold guides: [docs site — scaffolds](https://pwc-me-adv-strategyand.github.io/infra-platform-services/scaffolds/).

## Apps

Complete applications built on the SDKs and scaffolds live under [`apps/`](apps/):

| App | Folder | What it is |
| --- | --- | --- |
| M365 Workspace | [`apps/m365-workspace/`](apps/m365-workspace/) | Mail, calendar, contacts, SharePoint files and lists, and an LLM assistant — the whole Microsoft 365 SDK family used end to end. FastAPI backend from the backend scaffold, React + Vite + Tailwind + Lucide frontend from the frontend scaffold. |

## Golden rule

**Authorization is data (grants) enforced by the database (RLS), not scattered `if` statements.** Application code is the friendly first gate; RLS is the hard backstop.
