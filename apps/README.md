# Apps

Complete applications built on this repo's SDKs and scaffolds. Unlike
`scaffolds/` (templates that are copied) and `sdks/` (libraries that are
installed), an app is a runnable product: it consumes the SDKs as
dependencies and started life as a copy of the scaffolds.

| App | Folder | What it is |
| --- | --- | --- |
| M365 Workspace | [`m365-workspace/`](m365-workspace/) | Mail, calendar, contacts, SharePoint files and lists, and an LLM assistant — every Microsoft 365 SDK (`m365-client`, `outlook-client`, `sharepoint-client`, `m365-langchain-tools`) used end to end. FastAPI backend + React/Vite/Tailwind/Lucide frontend. |

## Conventions

- One folder per app with `backend/` and/or `frontend/`, each runnable after
  installing dependencies and filling `.env` — the app README covers the
  registration, permissions and env keys it needs.
- Apps consume SDKs as libraries (installed editable from a clone, from the
  Artifactory feed otherwise) — never by copying SDK code.
- Anything generic discovered while building an app goes back into the
  scaffold or SDK it belongs to; the app keeps only what is specific to it.
- Each app has a job in [`.github/workflows/apps-ci.yml`](../.github/workflows/apps-ci.yml).
