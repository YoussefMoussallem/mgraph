import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AuthShell from "./auth/AuthShell.jsx";
import AuthGate from "./auth/AuthGate.jsx";
import LoginPage from "./auth/LoginPage.jsx";
import { loginRequest } from "./auth/scopes.js";
import "./index.css";
import App from "./App.jsx";

// Composition root, as in the frontend scaffold:
//
//   AuthShell  — boots MSAL (redirect handling, active account) and
//                provides useAuth() to everything below.
//   /login     — the branded sign-in page; redirects home once signed in.
//   AuthGate   — guards every other route: validates the cached session
//                with a silent token call on initial load, redirects to
//                /login when there is none.
//
// The login request asks for the API scope up front (see auth/scopes.js),
// so consent for it is granted at sign-in and the first API call never
// bounces the user through a second consent prompt.
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <AuthShell
        clientId={import.meta.env.VITE_AZURE_CLIENT_ID}
        tenantId={import.meta.env.VITE_AZURE_TENANT_ID}
        loginRequest={loginRequest}
      >
        <Routes>
          <Route
            path="/login"
            element={
              <LoginPage
                productName="M365 Workspace"
                tagline="Your mailbox, calendar and SharePoint — with an assistant that works as you."
                loginRequest={loginRequest}
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
