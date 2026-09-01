import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Fresh module per test — invalidation.js keeps its debounce timestamp in
// module scope, so tests would otherwise leak state into each other.
async function loadInvalidation() {
  vi.resetModules();
  return import("../src/auth/invalidation.js");
}

describe("auth invalidation bridge", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Start well past the debounce window so the first call isn't
    // swallowed by the initial _lastInvalidationAt = 0.
    vi.setSystemTime(10_000);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("debounces a burst of concurrent 401s into one signOut", async () => {
    const { setAuthInvalidator, invalidateAuth } = await loadInvalidation();
    const signOut = vi.fn();
    setAuthInvalidator(signOut);

    invalidateAuth();
    invalidateAuth();
    invalidateAuth();

    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("fires again once the re-entry window has passed", async () => {
    const { setAuthInvalidator, invalidateAuth } = await loadInvalidation();
    const signOut = vi.fn();
    setAuthInvalidator(signOut);

    invalidateAuth();
    vi.setSystemTime(10_000 + 2001);
    invalidateAuth();

    expect(signOut).toHaveBeenCalledTimes(2);
  });

  it("is a no-op after the invalidator is unregistered", async () => {
    const { setAuthInvalidator, invalidateAuth } = await loadInvalidation();
    const signOut = vi.fn();
    setAuthInvalidator(signOut);
    setAuthInvalidator(null);

    invalidateAuth();

    expect(signOut).not.toHaveBeenCalled();
  });
});
