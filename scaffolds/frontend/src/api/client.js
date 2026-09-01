import { invalidateAuth } from "../auth/invalidation.js";

// HTTP path prefix. Relative — the Vite dev server proxy and production
// reverse proxy both handle the origin.
export const API_BASE = "/api";

// 401 = server has rejected our credentials. Could be: token expired
// without MSAL noticing, session revoked server-side, user account
// disabled in Entra, etc. Either way, the right action is "drop our
// auth state" — ``invalidateAuth`` calls the registered ``signOut``,
// which flips MSAL state, which the watchdog in App.jsx catches and
// redirects to /login. ``invalidateAuth`` is debounced internally so
// a burst of concurrent 401s doesn't trigger N signOuts.
export class UnauthorizedError extends Error {
  constructor(message = "Your session has expired. Please sign in again.") {
    super(message);
    this.name = "UnauthorizedError";
    this.status = 401;
  }
}

// 403 = "authenticated but not authorized for this resource". Distinct
// from 401: the credentials are good, the *permission* is missing.
// Thrown as a typed error so callers can surface a "you don't have
// access" UI inline rather than a generic error toast.
export class ForbiddenError extends Error {
  constructor(message = "You don't have access to this resource.") {
    super(message);
    this.name = "ForbiddenError";
    this.status = 403;
  }
}

// 5xx = "the server failed for transient reasons". Distinct from 4xx
// in that the *request* is fine — backend timeout, db blip, gateway
// error, deploy roll. Worth surfacing as a "something went wrong,
// retry" banner because the user can usually fix it by waiting a few
// seconds and trying again.
export class ServerError extends Error {
  constructor(message = "The server is having trouble. Please try again.", status = 500) {
    super(message);
    this.name = "ServerError";
    this.status = status;
  }
}

// Network failure (offline, CORS reject, DNS, fetch abort that wasn't
// user-initiated). Surfaced separately because the message to show is
// different ("you appear to be offline") and so is the retry advice.
export class NetworkError extends Error {
  constructor(message = "Couldn't reach the server. Check your connection.") {
    super(message);
    this.name = "NetworkError";
  }
}

// Guards use name/status rather than ``instanceof`` — class identity
// can flake across hot-reload boundaries.
export function isUnauthorized(err) {
  return err?.name === "UnauthorizedError" || err?.status === 401;
}

export function isForbidden(err) {
  return err?.name === "ForbiddenError" || err?.status === 403;
}

export function isServerError(err) {
  return err?.name === "ServerError" || (typeof err?.status === "number" && err.status >= 500);
}

export function isNetworkError(err) {
  return err?.name === "NetworkError";
}

function authHeaders(token) {
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

// Platform services return errors in a consistent envelope:
// ``{ "code": "string", "detail": "human message" }``. Prefer the
// human message when present; fall back to the HTTP status text.
async function errorDetail(res) {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    /* non-JSON error body — fall through */
  }
  return `Request failed: HTTP ${res.status}`;
}

/**
 * Fetch wrapper every API module builds on. Attaches the bearer token,
 * maps error statuses to typed errors, and routes 401s through the
 * auth-invalidation bridge.
 *
 * @param {string} path - Path under API_BASE, e.g. "/v1/projects".
 * @param {Object} [options]
 * @param {string|null} [options.token] - Bearer token from ``useToken()``'s getToken.
 * @param {string} [options.method]
 * @param {Object} [options.body] - JSON-serialised when provided.
 * @param {AbortSignal} [options.signal]
 * @param {Object} [options.headers] - Extra headers merged over the defaults.
 * @returns {Promise<any|null>} Parsed JSON body, or null for 204 responses.
 */
export async function apiFetch(path, { token, method = "GET", body, signal, headers } = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: { ...authHeaders(token), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    // User- or component-initiated aborts are not failures; rethrow so
    // callers' AbortController logic sees the original error.
    if (err?.name === "AbortError") throw err;
    throw new NetworkError();
  }

  if (res.status === 401) {
    invalidateAuth();
    throw new UnauthorizedError();
  }
  if (res.status === 403) throw new ForbiddenError(await errorDetail(res));
  if (res.status >= 500) throw new ServerError(await errorDetail(res), res.status);
  if (!res.ok) {
    const err = new Error(await errorDetail(res));
    err.status = res.status;
    throw err;
  }

  if (res.status === 204) return null;
  return res.json();
}
