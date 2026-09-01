// Vendored from frontend-comps (src/components/authentication/msalconfig.jsx).

/**
 * Creates an MSAL configuration object.
 *
 * @param {Object} options
 * @param {string} options.clientId - Azure AD application (client) ID.
 * @param {string} options.tenantId - Azure AD tenant ID.
 * @param {string} [options.redirectUri] - Redirect URI after login (defaults to window.location.origin).
 * @param {string} [options.postLogoutRedirectUri] - Redirect URI after logout (defaults to window.location.origin).
 * @param {boolean} [options.navigateToLoginRequestUrl] - Whether to navigate back to the original request URL after login (defaults to false).
 * @param {string} [options.cacheLocation] - Where to store auth cache: "localStorage" | "sessionStorage" (defaults to "localStorage").
 * @param {boolean} [options.storeAuthStateInCookie] - Whether to store auth state in a cookie (defaults to false).
 * @returns {Object} MSAL configuration object.
 */
export function createMsalConfig({
  clientId,
  tenantId,
  redirectUri = typeof window !== "undefined" ? window.location.origin : "/",
  postLogoutRedirectUri = typeof window !== "undefined"
    ? window.location.origin
    : "/",
  navigateToLoginRequestUrl = false,
  cacheLocation = "localStorage",
  storeAuthStateInCookie = false,
} = {}) {
  if (!clientId) throw new Error("createMsalConfig: clientId is required");
  if (!tenantId) throw new Error("createMsalConfig: tenantId is required");

  return {
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri,
      postLogoutRedirectUri,
      navigateToLoginRequestUrl,
    },
    cache: {
      cacheLocation,
      storeAuthStateInCookie,
    },
  };
}

/**
 * Creates a login request object with the given scopes.
 *
 * @param {string[]} [scopes] - OAuth scopes to request (defaults to ["openid", "profile", "email"]).
 * @returns {{ scopes: string[] }}
 */
export function createLoginRequest(
  scopes = ["openid", "profile", "email"]
) {
  return { scopes };
}

/** Default login request — provided for convenience. */
export const defaultLoginRequest = createLoginRequest();
