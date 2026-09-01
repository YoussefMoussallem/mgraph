import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import AuthGate from "../src/auth/AuthGate.jsx";

const msalState = vi.hoisted(() => ({
  instance: null,
  accounts: [],
  inProgress: "none",
}));

vi.mock("@azure/msal-react", () => ({
  useMsal: () => msalState,
}));

function renderGate(root) {
  return act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <AuthGate redirectTo="/login">
                <div>protected-content</div>
              </AuthGate>
            }
          />
          <Route path="/login" element={<div>login-screen</div>} />
        </Routes>
      </MemoryRouter>,
    );
  });
}

describe("AuthGate", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    msalState.instance = {
      getActiveAccount: vi.fn(() => ({ homeAccountId: "home-1" })),
      getAllAccounts: vi.fn(() => []),
      setActiveAccount: vi.fn(),
      acquireTokenSilent: vi.fn(() => Promise.resolve({ accessToken: "tok" })),
    };
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders children when silent token validation succeeds", async () => {
    await renderGate(root);
    expect(container.textContent).toContain("protected-content");
  });

  it("redirects to the login route when no account is cached", async () => {
    msalState.instance.getActiveAccount = vi.fn(() => null);
    await renderGate(root);
    expect(container.textContent).toContain("login-screen");
  });

  it("drops the active account on token failure without wiping app localStorage", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    window.localStorage.setItem("app.some_state", "keep-me");
    msalState.instance.acquireTokenSilent = vi.fn(() =>
      Promise.reject(new Error("interaction_required")),
    );

    await renderGate(root);

    expect(container.textContent).toContain("login-screen");
    expect(msalState.instance.setActiveAccount).toHaveBeenCalledWith(null);
    // The upstream frontend-comps version called localStorage.clear()
    // here; the scaffold deliberately preserves non-auth app state.
    expect(window.localStorage.getItem("app.some_state")).toBe("keep-me");
  });
});
