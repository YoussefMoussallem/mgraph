// Vendored from frontend-comps (src/components/authentication/AuthShell.jsx).
import { useEffect, useState } from "react";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { createMsalConfig } from "./msalConfig.js";
import { AuthProvider } from "./AuthProvider.jsx";

const DefaultBootstrapFallback = () => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
    <div>Initializing authentication...</div>
  </div>
);

/**
 * One-stop MSAL bootstrap: creates the PublicClientApplication, handles the
 * redirect promise, and mounts MsalProvider + AuthProvider around children.
 *
 * Consumers only need to provide clientId + tenantId. Everything else has
 * sensible defaults.
 *
 * @param {Object} props
 * @param {string} props.clientId - Azure AD application (client) ID.
 * @param {string} props.tenantId - Azure AD tenant ID.
 * @param {string} [props.redirectUri] - Override login redirect URI.
 * @param {string} [props.postLogoutRedirectUri] - Override logout redirect URI.
 * @param {"localStorage"|"sessionStorage"} [props.cacheLocation]
 * @param {boolean} [props.storeAuthStateInCookie]
 * @param {boolean} [props.navigateToLoginRequestUrl]
 * @param {{ scopes: string[] }} [props.loginRequest] - Forwarded to AuthProvider.
 * @param {string} [props.postLogoutRedirect] - Forwarded to AuthProvider.
 * @param {React.ReactNode} [props.loadingFallback] - Rendered while MSAL initializes.
 * @param {(error: Error) => void} [props.onInitError] - Called if MSAL init fails.
 * @param {React.ReactNode} props.children
 */
export default function AuthShell({
  clientId,
  tenantId,
  redirectUri,
  postLogoutRedirectUri,
  cacheLocation,
  storeAuthStateInCookie,
  navigateToLoginRequestUrl,
  loginRequest,
  postLogoutRedirect,
  loadingFallback = <DefaultBootstrapFallback />,
  onInitError,
  children,
}) {
  const [instance, setInstance] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const config = createMsalConfig({
      clientId,
      tenantId,
      redirectUri,
      postLogoutRedirectUri,
      cacheLocation,
      storeAuthStateInCookie,
      navigateToLoginRequestUrl,
    });
    const msal = new PublicClientApplication(config);

    msal
      .initialize()
      .then(() => msal.handleRedirectPromise())
      .then((result) => {
        if (cancelled) return;
        const account = result?.account || msal.getAllAccounts()[0];
        if (account) msal.setActiveAccount(account);
        setInstance(msal);
      })
      .catch((error) => {
        console.error("MSAL initialization failed:", error);
        if (onInitError) onInitError(error);
      });

    return () => {
      cancelled = true;
    };
  }, [
    clientId,
    tenantId,
    redirectUri,
    postLogoutRedirectUri,
    cacheLocation,
    storeAuthStateInCookie,
    navigateToLoginRequestUrl,
    onInitError,
  ]);

  if (!instance) return loadingFallback;

  return (
    <MsalProvider instance={instance}>
      <AuthProvider loginRequest={loginRequest} postLogoutRedirectUri={postLogoutRedirect}>
        {children}
      </AuthProvider>
    </MsalProvider>
  );
}
