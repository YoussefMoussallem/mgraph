import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AuthShell from "./auth/AuthShell.jsx";
import AuthGate from "./auth/AuthGate.jsx";
import LoginPage from "./auth/LoginPage.jsx";
import "./index.css";
import App from "./App.jsx";

// Composition root. Auth wraps the router in three layers:
//
//   AuthShell  — boots MSAL (redirect handling, active account) and
//                provides useAuth() to everything below.
//   /login     — the branded sign-in page; redirects home once signed in.
//   AuthGate   — guards every other route: validates the cached session
//                with a silent token call on initial load, redirects to
//                /login when there is none.
//
// Mid-session auth loss (expired session, 401 from the API, Entra
// revocation) is handled inside App.jsx — see the auth watchdog there.
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <AuthShell
        clientId={import.meta.env.VITE_AZURE_CLIENT_ID}
        tenantId={import.meta.env.VITE_AZURE_TENANT_ID}
      >
        <Routes>
          <Route
            path="/login"
            element={
              <LoginPage
                productName="Your App"
                tagline="Describe your product in one line"
              />
            }
          />
          <Route
            path="/*"
            element={
              <AuthGate redirectTo="/login">
                <App />
              </AuthGate>
            }
          />
        </Routes>
      </AuthShell>
    </BrowserRouter>
  </StrictMode>,
);
