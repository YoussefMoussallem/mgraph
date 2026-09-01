import { Suspense, lazy, useEffect } from "react";
import { Routes, Route, Navigate, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthProvider.jsx";
import { useSilentTokenRefresh } from "./auth/useSilentTokenRefresh.js";
import { setAuthInvalidator } from "./auth/invalidation.js";
import { API_SCOPES } from "./auth/scopes.js";
import Layout from "./components/Layout.jsx";
import { Loading } from "./components/ui.jsx";

// Each screen is its own chunk: the mail client is not paying for the
// Markdown renderer the assistant needs, and vice versa.
const MailPage = lazy(() => import("./screens/MailPage.jsx"));
const CalendarPage = lazy(() => import("./screens/CalendarPage.jsx"));
const ContactsPage = lazy(() => import("./screens/ContactsPage.jsx"));
const FilesPage = lazy(() => import("./screens/FilesPage.jsx"));
const AssistantPage = lazy(() => import("./screens/AssistantPage.jsx"));

export default function App() {
  // Auth watchdog (from the scaffold): AuthGate catches initial-load
  // unauthenticated state; this effect catches MSAL flipping mid-session
  // — explicit sign-out, Entra revocation, or a 401 routed through
  // invalidateAuth — and sends the user to /login.
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, loading: authLoading, signOut } = useAuth();

  useSilentTokenRefresh({
    enabled: isAuthenticated && !authLoading,
    // Keep the API-scope access token warm, not just the sign-in session.
    scopes: API_SCOPES,
    onInteractionRequired: () => {
      try { signOut(); } catch { /* best effort */ }
    },
  });

  useEffect(() => {
    if (authLoading || isAuthenticated) return;
    if (location.pathname === "/login") return;
    navigate("/login", { replace: true });
  }, [authLoading, isAuthenticated, location.pathname, navigate]);

  useEffect(() => {
    setAuthInvalidator(signOut);
    return () => setAuthInvalidator(null);
  }, [signOut]);

  return (
    <Layout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Navigate to="/mail" replace />} />
          <Route path="/mail" element={<MailPage />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/contacts" element={<ContactsPage />} />
          <Route path="/files" element={<FilesPage />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="*" element={<Navigate to="/mail" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
