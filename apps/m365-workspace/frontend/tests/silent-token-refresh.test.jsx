import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot } from "react-dom/client";
import { useSilentTokenRefresh } from "../src/auth/useSilentTokenRefresh.js";

const msalState = vi.hoisted(() => ({
  instance: null,
  accounts: [],
  inProgress: "none",
}));

vi.mock("@azure/msal-react", () => ({
  useMsal: () => msalState,
}));

function Harness({ enabled = true, onInteractionRequired }) {
  useSilentTokenRefresh({
    enabled,
    refreshIntervalMs: 1000,
    onInteractionRequired,
  });
  return null;
}

describe("useSilentTokenRefresh", () => {
  let container;
  let root;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    msalState.accounts = [{ homeAccountId: "home-1" }];
    msalState.inProgress = "none";
    msalState.instance = {
      getActiveAccount: vi.fn(() => null),
      acquireTokenSilent: vi.fn(() => Promise.resolve({ idToken: "fresh" })),
    };
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("silently refreshes on mount and interval", async () => {
    await act(async () => {
      root.render(<Harness />);
    });

    expect(msalState.instance.acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(msalState.instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: ["openid", "profile", "email"],
      account: msalState.accounts[0],
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(msalState.instance.acquireTokenSilent).toHaveBeenCalledTimes(2);
  });

  it("notifies when Entra requires interaction", async () => {
    const onInteractionRequired = vi.fn();
    msalState.instance.acquireTokenSilent = vi.fn(() =>
      Promise.reject({ errorCode: "interaction_required" }),
    );

    await act(async () => {
      root.render(<Harness onInteractionRequired={onInteractionRequired} />);
    });

    expect(onInteractionRequired).toHaveBeenCalledTimes(1);
  });
});
