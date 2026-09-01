# Scaffolds

Starter templates for building new AI Labs applications. Unlike `services/` (deployed once, consumed over HTTP) and `sdks/` (installed as dependencies), a scaffold is **copied** into a new project — the new app owns the copy and evolves it freely.

## Catalog

| Scaffold | Folder | What you get |
| --- | --- | --- |
| Frontend | [`frontend/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/scaffolds/frontend) | React + Vite SPA with Microsoft Entra ID auth wired end to end (login page, route guard, silent token refresh, authenticated fetch wrapper). Based on Edwin's client. |
| Backend | [`backend/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/scaffolds/backend) | FastAPI service with Entra ID JWT validation, platform error envelope, request-id tracing, an RS256 token test suite, and optional Microsoft Graph on-behalf-of wiring (`app/graph.py`). Based on Edwin's backend. |

The two scaffolds pair: the frontend acquires an Entra ID token via MSAL and the backend validates it against the same app registration — copy both to start a full-stack app.

## Conventions

- One folder per scaffold, each with its own `README.md` covering quickstart and architecture.
- Scaffolds must run out of the box after installing dependencies (`npm install` / `pip install -e ".[test]"`) and filling `.env` — no hidden setup.
- Improvements that would benefit every future app belong here (or upstream in the shared package they were vendored from), not in individual apps after they diverge.
