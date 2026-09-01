# M365 Workspace — backend

FastAPI service started from [`scaffolds/backend`](../../../scaffolds/backend/)
and wired for Microsoft Graph from day one. Everything the app can do is one
SDK call per route:

| Router | Prefix | SDK |
| --- | --- | --- |
| `app/routes/outlook.py` | `/api/v1/outlook` | `outlook-client` — folders, messages (list/search/read/send/draft/attach/reply/forward/move/delete/read-state), attachments, events (calendar view/create/update/respond/delete), contacts, profile |
| `app/routes/sharepoint.py` | `/api/v1/sharepoint` | `sharepoint-client` — sites, drives, lists and list items, items by path, search, content, upload, folders, move/rename, delete |
| `app/routes/agent.py` + `app/agent.py` | `/api/v1/agent` | `m365-langchain-tools` bound per request; LangChain tool-calling loop over any OpenAI-compatible model |
| `app/routes/me.py` | `/api/v1/me` | the scaffold's identity probe |

Identity, the error envelope and the Graph error mapping are the scaffold's
(`app/dependencies.py`, `app/middleware/`, `app/graph.py`); `app/main.py`
adds one handler that turns the SDKs' argument checks (`ValueError`) into
400 `bad_request`.

```bash
pip install -e ../../../sdks/python/m365/m365-client -e ../../../sdks/python/m365/outlook-client \
  -e ../../../sdks/python/m365/sharepoint-client -e ../../../sdks/python/m365/langchain-tools
pip install -e ".[test]"
cp .env.example .env         # registration, client secret, API_REQUIRED_SCOPE=access_as_user, optional LLM
uvicorn app.main:app --reload
pytest                       # 82 tests, no tenant needed: mock Graph behind a real M365Client
```

OpenAPI docs at `/docs` once running. Setup, permissions and the design
notes are in the [app README](../README.md).
