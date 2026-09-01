// The token the backend needs.
//
// The backend calls Microsoft Graph on the caller's behalf, and the only
// thing Entra will exchange on-behalf-of is an ACCESS token issued for the
// API's own exposed scope — never the ID token the scaffold sends by
// default. Every token request in this app therefore asks for that scope:
// the login request (so consent is captured at sign-in), the silent
// refresh, and ``useToken``.
//
// The registration exposes the scope as api://<client-id>/<name>;
// VITE_API_SCOPE overrides the whole string when the name differs.

function resolveApiScope() {
  const explicit = import.meta.env.VITE_API_SCOPE;
  if (explicit) return explicit;
  return `api://${import.meta.env.VITE_AZURE_CLIENT_ID}/access_as_user`;
}

export const API_SCOPE = resolveApiScope();
export const API_SCOPES = [API_SCOPE];

/** Login request: the API scope (openid/profile are implied by MSAL). */
export const loginRequest = { scopes: [...API_SCOPES] };
