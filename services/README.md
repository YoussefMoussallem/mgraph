# Services

Each subdirectory is an independently deployable microservice. Services communicate over HTTP with platform JWTs (and M2M tokens for service-to-service calls).

| Folder | Service ID | Description |
| --- | --- | --- |
| [identity/](identity/) | SS-01 | Identity & SSO |
| [directory/](directory/) | SS-02 | User Directory |
| [genai/](genai/) | SS-03 | GenAI proxy |
| [sharing/](sharing/) | — | Universal Sharing Framework |
| [knowledge-base/](knowledge-base/) | SS-05 | Knowledge Base / RAG |
| [storage/](storage/) | SS-12 | File / Blob Storage broker |

## Adding a new service

1. Create a folder under `services/<name>/`
2. Add a service doc under `docs/services/`, and add it to the `Services` nav in [`mkdocs.yml`](../mkdocs.yml) — the docs site builds with `--strict`, so a doc that is not in the nav fails CI
3. Add SDK documentation under `sdks/` (see [`sdks/README.md`](../sdks/README.md))
4. Update the catalog table in the root `README.md`
5. Add a test job for the service to [`.github/workflows/services-ci.yml`](../.github/workflows/services-ci.yml) so its tests run on every PR (lint already covers the whole folder)

## Microsoft 365 is an SDK, not a service

Outlook and SharePoint access is delivered as libraries an app embeds — [`outlook-client`](../sdks/python/m365/outlook-client/) and [`sharepoint-client`](../sdks/python/m365/sharepoint-client/), both on the [`m365-client`](../sdks/python/m365/m365-client/) foundation — rather than as proxy services. Every Graph call is made as the signed-in user, so nothing an app reads through them is anything its caller could not read themselves.

A service that needs Microsoft 365 data should consume those SDKs the way the `m365-client` README describes — a config layer that turns env into `M365Settings`, one `M365Client` built in the FastAPI lifespan, and the backend scaffold's `get_graph` per request wrapped in `OutlookClient` / `SharePointClient` — rather than talk to Graph directly. Adding an app-only path anywhere is a deliberate, reviewable decision, not a refactor.
