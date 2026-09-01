// Vendored from frontend-comps (src/components/authentication/AuthGate.jsx),
// with one deliberate change: on token-validation failure the upstream
// version calls ``localStorage.clear()``, which wipes *all* app state
// (persisted UI prefs, last-opened ids, ...) along with the MSAL cache.
// Here we only drop the active account — MSAL owns its own cache entries
// and the redirect to /login is what actually ends the session.
import { Navigate } from "react-router-dom";
import { useMsal } from "@azure/msal-react";
import { useEffect, useState } from "react";
import { defaultLoginRequest } from "./msalConfig.js";

const DefaultLoader = () => (
  <div className="flex items-center justify-center h-screen">
    <div>Checking authentication...</div>
  </div>
);

/**
 * Guards a route behind MSAL authentication. Redirects unauthenticated users.
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children - Content to render when authenticated.
 * @param {{ scopes: string[] }} [props.loginRequest] - Token request scopes (defaults to defaultLoginRequest).
 * @param {string} [props.redirectTo] - Path to redirect unauthenticated users (defaults to "/login").
 * @param {React.ComponentType} [props.LoadingComponent] - Component shown while checking auth (defaults to a simple spinner message).
 * @param {(account: object) => void} [props.onAuthenticated] - Optional callback invoked with the active account after successful validation.
 */
export default function AuthGate({
  children,
  loginRequest = defaultLoginRequest,
  redirectTo = "/login",
  LoadingComponent = DefaultLoader,
  onAuthenticated,
}) {
  const { instance } = useMsal();
  const [isValidAuth, setIsValidAuth] = useState(false);
  const [isChecking, setIsChecking] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      const account =
        instance.getActiveAccount() || instance.getAllAccounts()[0];

      if (!account) {
        setIsValidAuth(false);
        setIsChecking(false);
        return;
      }

      try {
        const response = await instance.acquireTokenSilent({
          ...loginRequest,
          account,
        });

        const valid = !!response.accessToken;
        setIsValidAuth(valid);

        if (valid && onAuthenticated) {
          onAuthenticated(account);
        }
      } catch (error) {
        console.error("Token validation failed:", error);
        instance.setActiveAccount(null);
        setIsValidAuth(false);
      } finally {
        setIsChecking(false);
      }
    };

    checkAuth();
  }, [instance, loginRequest, onAuthenticated]);

  if (isChecking) {
    return <LoadingComponent />;
  }

  return isValidAuth ? children : <Navigate to={redirectTo} replace />;
}
