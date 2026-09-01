# M365 Workspace

A full-stack reference application that consumes the whole Microsoft 365 SDK
family as libraries: mail, attachments, calendar and contacts through
[`outlook-client`](../../sdks/python/m365/outlook-client/), sites, document
libraries, files and lists through
[`sharepoint-client`](../../sdks/python/m365/sharepoint-client/), and an
assistant that gets the same workloads as LLM tools through
[`m365-langchain-tools`](../../sdks/python/m365/langchain-tools/) — all on
[`m365-client`](../../sdks/python/m365/m365-client/)'s on-behalf-of token
exchange, so every call runs as the signed-in user.

Both halves were started from the repo's scaffolds and own their copies:
[`backend/`](backend/) from [`scaffolds/backend`](../../scaffolds/backend/)
(FastAPI, Entra JWT validation, the `{code, detail}` envelope, `app/graph.py`)
and [`frontend/`](frontend/) from [`scaffolds/frontend`](../../scaffolds/frontend/)
(React 19 + Vite + Tailwind v4, MSAL). Icons are [Lucide](https://lucide.dev).

```
apps/m365-workspace/
├── backend/                 FastAPI — the SDKs behind /api/v1/...
│   ├── app/routes/outlook.py      outlook-client: 23 endpoints
│   ├── app/routes/sharepoint.py   sharepoint-client: 15 endpoints
│   ├── app/routes/agent.py        m365-langchain-tools bound per request
│   ├── app/agent.py               the tool-calling loop (LangChain, any OpenAI-compatible model)
│   └── tests/                     82 tests: real SDKs against a mock Graph, scripted LLM
└── frontend/                React SPA — Mail · Calendar · Contacts · Files · Assistant
    ├── src/api/                   outlook.js / sharepoint.js / agent.js over the scaffold's apiFetch
    ├── src/auth/scopes.js         the API scope every token request asks for
    └── src/screens/               one screen per workload
```

## What it does

| Screen | Backed by | Capabilities |
| --- | --- | --- |
| **Mail** | `OutlookClient` | Folders with unread counts · list, search (`from:`, `subject:`, `hasAttachments:true`), unread filter · read (HTML in a sandboxed iframe) · attachments download · compose, save draft, attach, send draft · reply / reply-all / forward · archive, move, delete, mark unread |
| **Calendar** | `OutlookClient` | Week view via calendar view (recurrences expanded) · event detail with attendees and responses · create with invitations and Teams link · accept / tentative / decline · cancel or remove |
| **Contacts** | `OutlookClient` | Prefix search over personal contacts |
| **Files** | `SharePointClient` | Site search → libraries → folder navigation by path · library-wide search · text preview · download · upload (`rename` on conflict) · new folder · rename / move · delete to recycle bin · site lists with their column values |
| **Assistant** | `m365_tools()` | Chat with the 13 read tools, or all 24 with *Allow actions* on · every tool call shown with arguments and result · palette of bound tools |

## Prerequisites: the app registration

One Entra ID app registration serves both halves (the SPA signs in against
it; the backend validates its tokens and exchanges them for Graph):

1. **Platform: Single-page application** with the dev origin
   (`http://localhost:5173`) as a redirect URI.
2. **Expose an API** scope: `api://<client-id>/access_as_user`. Add the SPA's
   own client id under *Authorized client applications* so the scope is
   consented in the same sign-in.
3. **A client credential** (secret or certificate) — the on-behalf-of
   exchange is a confidential-client operation.
4. **Delegated Graph permissions**, admin-consented. For everything the app
   can do: `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`,
   `Contacts.Read`, `Sites.ReadWrite.All`, `Files.ReadWrite.All`. A read-only
   deployment needs only `User.Read`, `Mail.Read`, `Calendars.Read`,
   `Contacts.Read`, `Sites.Read.All`, `Files.Read.All` — Graph checks per
   call, so a missing permission surfaces as a 403 `graph_forbidden`, never a
   silent no-op. Each SDK README lists permissions by operation.

Consent is granted once, by an admin, for the whole set: the backend's
exchange cannot prompt a user, so an un-consented scope fails as
`m365_auth_failed` with `AADSTS65001`.

## Run it

Backend (from a clone, with the SDKs installed editable):

```bash
cd apps/m365-workspace/backend
pip install -e ../../../sdks/python/m365/m365-client -e ../../../sdks/python/m365/outlook-client \
  -e ../../../sdks/python/m365/sharepoint-client -e ../../../sdks/python/m365/langchain-tools
pip install -e ".[test]"
cp .env.example .env      # AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET; API_REQUIRED_SCOPE=access_as_user
uvicorn app.main:app --reload
```

Frontend:

```bash
cd apps/m365-workspace/frontend
npm install
cp .env.example .env      # VITE_AZURE_CLIENT_ID, VITE_AZURE_TENANT_ID (same registration)
npm run dev               # http://localhost:5173, /api proxied to the backend
```

The assistant tab stays disabled (with a message) until `OPENAI_API_KEY` and
`AGENT_MODEL` are set — any OpenAI-compatible endpoint works
(`OPENAI_BASE_URL` for Azure OpenAI behind a proxy, LiteLLM, …). Everything
else works without an LLM.

Tests: `pytest` in `backend/` (no tenant needed — see below), `npm test`
and `npm run build` in `frontend/`.

## How the pieces fit

**Identity.** The SPA asks MSAL for an access token for the API scope
(`src/auth/scopes.js`), not the ID token the scaffold sends by default;
`src/auth/useToken.js` redirects to sign-in when consent is missing instead
of signing the user out. The backend runs in `API_REQUIRED_SCOPE` mode, so
`get_current_user` keeps the raw token as the on-behalf-of assertion and
`app/graph.py::get_graph` hands every route a `GraphServiceClient` acting as
the caller. Nothing in the app chooses an identity; Graph enforces the
user's own permissions.

**Routes are one SDK call each.** `OutlookClient(graph).<method>(...)`,
`SharePointClient(graph).<method>(...)` plus request/response shaping. SDK
errors propagate to the scaffold's handlers (`graph_not_found` 404,
`graph_forbidden` 403, `graph_throttled` 429 with `Retry-After`,
`m365_auth_failed` 502) and the SDKs' argument checks (`ValueError`) become
400 `bad_request` with the SDK's own message (`app/main.py`). Binary
responses (attachments, file content) carry the file name and MIME type
from the SDK's metadata.

**The assistant is the host integration the tools package documents.**
`app/routes/agent.py` builds a `graph_provider` from the request's Graph
client, constructs fresh tools with `m365_tools(provider,
include_writes=...)`, and `app/agent.py` runs a plain LangChain
tool-calling loop (`bind_tools`, `ToolMessage`s, eight-step budget).
Recoverable tool problems come back as text the model corrects; results are
the tools' own JSON, shown in the UI as steps. The system prompt tells the
model to prefer reads and drafts unless the user asked for exactly that
action — and *Allow actions* off means the write tools do not exist for the
run at all.

**Tests need no tenant.** `tests/graph_mock.py` is a mock Graph behind a
real `M365Client` (stubbed credentials, `httpx.MockTransport`), so every
route test runs the SDKs' real request building, paging and error
translation; the agent tests replace only the LLM with a scripted model and
assert on the tool steps that came back through the mock.

## Related

- [`sdks/python/m365/`](../../sdks/python/m365/) — the SDK family, its
  permission tables and the token contract.
- [`scaffolds/`](../../scaffolds/) — where both halves came from; generic
  improvements belong there, not here.
