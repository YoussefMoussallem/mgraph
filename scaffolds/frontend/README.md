# Frontend scaffold

Starter template for AI Labs frontends. React + Vite SPA with Microsoft Entra ID (Azure AD) authentication wired end to end: branded login page, route guarding, silent token refresh, and a fetch wrapper that signs requests and signs the user out when the server rejects them.

Extracted from Edwin's client (`edwin/client/app`) and the shared `frontend-comps` package, so a new app starts with the same auth behaviour Edwin has in production.

## Starting a new app from this scaffold

1. Copy `scaffolds/frontend/` into your project (it is a template, not a dependency — you own the copy).
2. `npm install`
3. Create an Entra ID **app registration** of type *Single-page application* with your dev origin (e.g. `http://localhost:5173`) as a redirect URI.
4. `cp .env.example .env` and fill in `VITE_AZURE_CLIENT_ID` + `VITE_AZURE_TENANT_ID`.
5. `npm run dev` — you should be able to sign in with Microsoft and land on the placeholder home screen.
6. Replace `src/screens/HomePage.jsx`, the product name/tagline in `src/main.jsx`, and the `<title>` in `index.html`.

Run tests with `npm test`.

## How authentication works

### Layers (composition in `src/main.jsx`)

| Layer | File | Job |
| --- | --- | --- |
| `AuthShell` | `src/auth/AuthShell.jsx` | Boots MSAL: creates the `PublicClientApplication`, awaits `initialize()` + `handleRedirectPromise()`, sets the active account, mounts `MsalProvider` + `AuthProvider`. Renders a loading fallback until MSAL is ready. |
| `AuthProvider` / `useAuth()` | `src/auth/AuthProvider.jsx` | React context exposing `user`, `isAuthenticated`, `loading`, `signInWithMicrosoft` (login redirect), `signOut` (logout redirect → `/login`). |
| `LoginPage` | `src/auth/LoginPage.jsx` | Branded Strategy& sign-in page at `/login`. Redirects home (with a fade) once authenticated. |
| `AuthGate` | `src/auth/AuthGate.jsx` | Route guard around everything except `/login`. On initial load it validates the cached session with `acquireTokenSilent`; unauthenticated users are redirected to `/login`. |

### Session lifecycle (wired in `src/App.jsx`)

- **Silent refresh** — `src/auth/useSilentTokenRefresh.js` refreshes the MSAL token cache every 45 minutes and whenever the window regains focus/visibility, so API calls never race an expired token after the tab sat idle. If Entra demands interactive sign-in (Conditional Access session lifetime, revocation), the app signs out and the watchdog redirects to `/login`.
- **Auth watchdog** — an effect in `App.jsx` watches `isAuthenticated` and redirects to `/login` the moment MSAL state flips mid-session (explicit sign-out, Entra revocation, invalidation from a 401).
- **401 invalidation bridge** — `src/auth/invalidation.js` is a module-level singleton connecting non-React code to `signOut`. `App.jsx` registers `signOut` on boot; the API client calls `invalidateAuth()` on any 401. Debounced so a burst of concurrent 401s triggers one sign-out, not N.

### Calling your API

- `src/auth/useToken.js` → `getToken()` — call before every request; resolves the Entra **ID token** via `acquireTokenSilent` (the backend validates it against the tenant; no custom API scope). On silent failure it signs the user out and returns `null`.
- `src/api/client.js` → `apiFetch(path, { token, ... })` — attaches `Authorization: Bearer`, maps responses to typed errors (`UnauthorizedError`, `ForbiddenError`, `ServerError`, `NetworkError`), routes 401s through the invalidation bridge, and reads the platform error envelope (`{ code, detail }`).

```jsx
import { useToken } from "./auth/useToken.js";
import { apiFetch, isForbidden } from "./api/client.js";

function useProjects() {
  const getToken = useToken();
  return async () => {
    const token = await getToken();
    if (!token) return [];            // session died — watchdog is redirecting
    try {
      return await apiFetch("/v1/projects", { token });
    } catch (err) {
      if (isForbidden(err)) return []; // show "no access" UI instead of a toast
      throw err;
    }
  };
}
```

`useCurrentUserOid()` (also in `useToken.js`) returns the signed-in user's Azure AD object id — the stable identifier backends should key users on.

### Identity service (SS-01) extension point

Today the SPA sends the Entra ID token straight to the backend. When the [SS-01 identity service](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/identity) ships its Entra-token → platform-JWT exchange, `useToken.js` is the single place to change: exchange the Entra token there and return the platform JWT. Nothing else in the app needs to know.

## Differences from the sources

- **Auth components are vendored, not imported.** Edwin depends on `frontend-comps` from npm; here `AuthShell`/`AuthProvider`/`AuthGate`/`LoginPage`/`msalConfig` are copied into `src/auth/` so each new app owns its auth code outright. Each vendored file notes its origin at the top.
- **`AuthGate` no longer calls `localStorage.clear()`** on token-validation failure. The upstream version wiped all app state along with the MSAL cache; the scaffold only drops the active account (covered by a test).
- Edwin-specific concerns (SSE constants, deck/share-link handling, export aliases) are stripped; what remains is the generic auth + API skeleton.

## Layout

```
scaffolds/frontend/
├── index.html            # Entry HTML (fonts, #root)
├── vite.config.js        # React + Tailwind plugins, /api dev proxy, vitest config
├── .env.example          # VITE_AZURE_CLIENT_ID / VITE_AZURE_TENANT_ID / proxy target
├── src/
│   ├── main.jsx          # Composition root: AuthShell → /login + AuthGate → App
│   ├── App.jsx           # Auth watchdog, silent refresh, invalidator registration, routes
│   ├── index.css         # Tailwind + brand theme tokens (swap --color-brand to re-skin)
│   ├── auth/             # Everything authentication (vendored comps + lifecycle hooks)
│   ├── api/client.js     # apiFetch + typed errors
│   ├── assets/           # Strategy& logo used by LoginPage
│   └── screens/          # HomePage placeholder — replace with your app
└── tests/                # Vitest + jsdom: silent refresh, AuthGate, 401 debounce
```

## Dependencies and why

| Package | Why |
| --- | --- |
| `@azure/msal-browser` + `@azure/msal-react` | Microsoft's supported OAuth/OIDC client for SPAs — token cache, redirect flow, silent renewal. |
| `react`, `react-dom` | UI runtime (React 19, matching Edwin). |
| `react-router-dom` | Routing; the auth flow hinges on the `/login` vs `/*` split. |
| `tailwindcss` + `@tailwindcss/vite` | Styling with the brand theme tokens in `index.css`. |
| `vite`, `@vitejs/plugin-react` | Build/dev server, `/api` proxy to the backend. |
| `vitest`, `jsdom` | Test runner + DOM environment for the auth tests. |
