import { useEffect } from "react";
import { Routes, Route, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthProvider.jsx";
import { useSilentTokenRefresh } from "./auth/useSilentTokenRefresh.js";
import { setAuthInvalidator } from "./auth/invalidation.js";
import HomePage from "./screens/HomePage.jsx";

export default function App() {
  // Auth watchdog. ``AuthGate`` (in main.jsx) catches *initial-load*
  // unauthenticated state, but it doesn't run mid-session. If MSAL state
  // flips to unauthenticated while the user is in-app — explicit
  // ``signOut`` from the UI, Entra revoking the session, the silent
  // token failure path in useToken calling signOut, or a 401 from any
  // API call routing through invalidateAuth — this effect picks it up
  // and redirects to /login. ``loading`` is checked because MSAL
  // briefly reports unauthenticated during initial bootstrap.
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, loading: authLoading, signOut } = useAuth();

  useSilentTokenRefresh({
    enabled: isAuthenticated && !authLoading,
    onInteractionRequired: () => {
      // Conditional Access policies may require an interactive sign-in
      // after their configured session lifetime. Proactive silent refresh
      // handles the normal cached-token path; when Entra says interaction
      // is required, signing out hands control to the watchdog below.
      try { signOut(); } catch { /* best effort */ }
    },
  });

  useEffect(() => {
    if (authLoading || isAuthenticated) return;
    // Redirect-loop guard: ``main.jsx`` only mounts ``<App />`` under
    // ``/*`` (not ``/login``) so we shouldn't ever observe
    // ``isAuthenticated=false`` while on /login — but if the routing
    // ever changes this prevents a runaway loop.
    if (location.pathname === "/login") return;
    navigate("/login", { replace: true });
  }, [authLoading, isAuthenticated, location.pathname, navigate]);

  // Register the auth-invalidation callback so non-React modules
  // (api/client.js, SSE readers) can trigger a logout when the server
  // rejects our credentials. Without this, a server-side session
  // revocation (token still valid by MSAL but the server has dropped
  // the session) would loop on 401s with no redirect.
  useEffect(() => {
    setAuthInvalidator(signOut);
    return () => setAuthInvalidator(null);
  }, [signOut]);

  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
    </Routes>
  );
}
