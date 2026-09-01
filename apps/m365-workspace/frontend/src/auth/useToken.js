import { useCallback } from "react";
import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { useMsal } from "@azure/msal-react";
import { useAuth } from "./AuthProvider.jsx";
import { API_SCOPES } from "./scopes.js";

/**
 * Returns an async ``getToken`` callback that API code calls before every
 * request. Resolves to an **access token for this API's own scope** (see
 * ``scopes.js``) — the one change from the frontend scaffold, which returns
 * the ID token. The backend's ``API_REQUIRED_SCOPE`` mode validates the
 * ``scp`` claim and exchanges the token on-behalf-of for Microsoft Graph.
 *
 * When Entra wants interaction — consent for the API scope was never
 * granted, or a Conditional Access session lifetime ran out — the user is
 * redirected to sign in again rather than signed out: they come back with
 * the token. Any other silent failure is the canonical "session is dead"
 * signal and signs the user out (the watchdog in App.jsx redirects).
 */
export function useToken() {
  const { instance, accounts } = useMsal();
  const { signOut } = useAuth();

  const getToken = useCallback(async () => {
    const account = instance.getActiveAccount() || accounts[0];
    if (!account) return null;

    try {
      const response = await instance.acquireTokenSilent({ scopes: API_SCOPES, account });
      return response.accessToken;
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) {
        await instance.acquireTokenRedirect({ scopes: API_SCOPES, account });
        return null;
      }
      try { signOut(); } catch { /* best effort */ }
      return null;
    }
  }, [instance, accounts, signOut]);

  return getToken;
}

/**
 * The signed-in user's Azure AD object id — the stable identifier the
 * backend keys users on (``CurrentUser.user_id``).
 */
export function useCurrentUserOid() {
  const { accounts } = useMsal();
  return accounts[0]?.localAccountId || null;
}
