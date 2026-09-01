import { useCallback } from "react";
import { useMsal } from "@azure/msal-react";
import { useAuth } from "./AuthProvider.jsx";

const TOKEN_SCOPES = ["openid", "profile", "email"];

/**
 * Returns an async ``getToken`` callback that API code calls before every
 * request. Resolves to the Entra **ID token** — the backend validates it
 * directly against the tenant (no custom API scope is registered).
 *
 * If the platform later adopts the SS-01 identity service's
 * Entra-token → platform-JWT exchange, this hook is the single place to
 * change: swap the return value for the exchanged platform JWT and the
 * rest of the app is untouched.
 */
export function useToken() {
  const { instance, accounts } = useMsal();
  // ``signOut`` comes from AuthProvider rather than calling
  // ``instance.logoutRedirect`` directly — keeps the logout flow in one
  // place so the redirect URI behaviour stays consistent across the app.
  const { signOut } = useAuth();

  const getToken = useCallback(async () => {
    const account = accounts[0];
    if (!account) return null;

    try {
      const response = await instance.acquireTokenSilent({
        scopes: TOKEN_SCOPES,
        account,
      });
      return response.idToken;
    } catch {
      // Silent acquisition failure is the canonical "session is dead"
      // signal in MSAL — usually InteractionRequiredAuthError because
      // Entra revoked the session, the refresh token expired, or the
      // user signed out elsewhere. ``signOut`` flips MSAL state, the
      // auth watchdog in App.jsx then navigates to /login. We also
      // still return null so the caller's API call short-circuits
      // without an Authorization header rather than firing with a
      // stale token.
      try { signOut(); } catch { /* ignore — best effort */ }
      return null;
    }
  }, [instance, accounts, signOut]);

  return getToken;
}

/**
 * The signed-in user's Azure AD object id — the stable identifier the
 * backend should key users on. Used by frontend code that needs to
 * compare "who am I?" against rows returned by the API (e.g. labelling
 * the current user's row "(you)" in a sharing dialog).
 */
export function useCurrentUserOid() {
  const { accounts } = useMsal();
  return accounts[0]?.localAccountId || null;
}
