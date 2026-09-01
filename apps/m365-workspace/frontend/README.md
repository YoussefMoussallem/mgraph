# M365 Workspace — frontend

React 19 + Vite + Tailwind v4 SPA started from
[`scaffolds/frontend`](../../../scaffolds/frontend/), with
[Lucide](https://lucide.dev) icons. The scaffold's MSAL bootstrap, route
guard, silent refresh, 401 invalidation bridge and `apiFetch` are kept
as-is; what changed and what was added:

| Path | Role |
| --- | --- |
| `src/auth/scopes.js` | The API scope (`api://<client-id>/access_as_user`) every token request asks for — the backend exchanges that token for Microsoft Graph. |
| `src/auth/useToken.js` | Returns the **access token** for that scope (the scaffold returns the ID token) and redirects to sign-in when consent is missing. |
| `src/api/client.js` | Scaffold's fetch wrapper plus `apiUpload` (multipart) and `apiFetchBlob` (downloads with the server's file name). |
| `src/api/outlook.js`, `sharepoint.js`, `agent.js` | One function per backend endpoint. |
| `src/hooks/useApi.js` | `useRequest` (declarative loads), `useAction` (mutations with busy/error state). |
| `src/screens/` | Mail, Calendar, Contacts, Files, Assistant — lazy-loaded chunks. |
| `src/components/` | Layout with navigation, UI primitives, the compose dialog, a sandboxed HTML body renderer. |

```bash
npm install
cp .env.example .env         # VITE_AZURE_CLIENT_ID, VITE_AZURE_TENANT_ID — same registration as the backend
npm run dev                  # http://localhost:5173, /api proxied to http://127.0.0.1:8000
npm test && npm run build
```

Setup and permissions are in the [app README](../README.md).
