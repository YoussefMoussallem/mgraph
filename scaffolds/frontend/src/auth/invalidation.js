// Module-level singleton that lets non-React code (api/client.js, SSE
// readers, anything that does a fetch) trigger an auth-state flip when
// the server says our credentials are dead.
//
// Why a module-level singleton instead of passing ``signOut`` through
// every API call:
//
//  - The API client exports many functions consumed all over the app.
//    Threading a second arg through every signature is invasive churn.
//  - ``signOut`` only meaningfully exists once per session. There's no
//    case where two different callers want to invalidate two different
//    auth contexts.
//  - Hooks can't be called from plain modules. A singleton bridge
//    (registered once via useEffect at app boot) is the standard React
//    pattern for "non-React module needs access to a stable callback".
//
// Lifecycle: ``App.jsx`` calls ``setAuthInvalidator(signOut)`` in a
// ``useEffect`` and clears it on unmount. Any fetch wrapper that sees a
// 401 calls ``invalidateAuth()`` — that triggers MSAL signOut, which
// flips ``isAuthenticated``, which the auth-watchdog effect then
// catches and navigates to /login.
//
// Re-entry guard: ``invalidateAuth`` is idempotent within a short
// window so a burst of in-flight requests all 401-ing at once doesn't
// fire signOut N times.

let _invalidator = null;
let _lastInvalidationAt = 0;
const REENTRY_WINDOW_MS = 2000;

export function setAuthInvalidator(fn) {
  _invalidator = typeof fn === "function" ? fn : null;
}

export function invalidateAuth() {
  if (!_invalidator) return;
  const now = Date.now();
  if (now - _lastInvalidationAt < REENTRY_WINDOW_MS) return;
  _lastInvalidationAt = now;
  try {
    _invalidator();
  } catch {
    /* best effort — signOut may throw if called during teardown */
  }
}
