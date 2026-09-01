import { invalidateAuth } from "../auth/invalidation.js";

// HTTP path prefix. Relative — the Vite dev server proxy and production
// reverse proxy both handle the origin.
export const API_BASE = "/api";

// 401 = server has rejected our credentials. The right action is "drop our
// auth state" — ``invalidateAuth`` calls the registered ``signOut``, which
// flips MSAL state, which the watchdog in App.jsx catches and redirects to
// /login. Debounced internally so a burst of 401s triggers one sign-out.
export class UnauthorizedError extends Error {
  constructor(message = "Your session has expired. Please sign in again.") {
    super(message);
    this.name = "UnauthorizedError";
    this.status = 401;
  }
}

// 403 = authenticated but not authorized. With Microsoft Graph behind the
// API this is usually a delegated permission that was never granted or
// consented — the backend's ``graph_forbidden`` envelope says so.
export class ForbiddenError extends Error {
  constructor(message = "You don't have access to this resource.") {
    super(message);
    this.name = "ForbiddenError";
    this.status = 403;
  }
}

// 5xx = the server failed for transient reasons; a failed on-behalf-of
// exchange (``m365_auth_failed``, 502) also lands here with its AADSTS code.
export class ServerError extends Error {
  constructor(message = "The server is having trouble. Please try again.", status = 500) {
    super(message);
    this.name = "ServerError";
    this.status = status;
  }
}

// Network failure (offline, CORS reject, DNS, non-user abort).
export class NetworkError extends Error {
  constructor(message = "Couldn't reach the server. Check your connection.") {
    super(message);
    this.name = "NetworkError";
  }
}

// Guards use name/status rather than ``instanceof`` — class identity can
// flake across hot-reload boundaries.
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
  const h = {};
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

// Platform services return errors in a consistent envelope:
// ``{ "code": "string", "detail": "human message" }``. Prefer the human
// message when present; fall back to the HTTP status text. The code is
// kept on the thrown error so callers can branch without string-matching.
async function errorInfo(res) {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail) {
      return { detail: body.detail, code: body.code };
    }
  } catch {
    /* non-JSON error body — fall through */
  }
  return { detail: `Request failed: HTTP ${res.status}`, code: undefined };
}

async function doFetch(url, init) {
  try {
    return await fetch(url, init);
  } catch (err) {
    // User- or component-initiated aborts are not failures; rethrow so
    // callers' AbortController logic sees the original error.
    if (err?.name === "AbortError") throw err;
    throw new NetworkError();
  }
}

async function throwForStatus(res) {
  if (res.ok) return;
  if (res.status === 401) {
    invalidateAuth();
    throw new UnauthorizedError();
  }
  const { detail, code } = await errorInfo(res);
  if (res.status === 403) {
    const err = new ForbiddenError(detail);
    err.code = code;
    throw err;
  }
  if (res.status >= 500) {
    const err = new ServerError(detail, res.status);
    err.code = code;
    throw err;
  }
  const err = new Error(detail);
  err.status = res.status;
  err.code = code;
  throw err;
}

/**
 * Fetch wrapper every API module builds on. Attaches the bearer token,
 * maps error statuses to typed errors, and routes 401s through the
 * auth-invalidation bridge.
 *
 * @param {string} path - Path under API_BASE, e.g. "/v1/outlook/messages".
 * @param {Object} [options]
 * @param {string|null} [options.token] - Bearer token from ``useToken()``'s getToken.
 * @param {string} [options.method]
 * @param {Object} [options.body] - JSON-serialised when provided.
 * @param {AbortSignal} [options.signal]
 * @param {Object} [options.headers] - Extra headers merged over the defaults.
 * @returns {Promise<any|null>} Parsed JSON body, or null for 204 responses.
 */
export async function apiFetch(path, { token, method = "GET", body, signal, headers } = {}) {
  const res = await doFetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders(token), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  await throwForStatus(res);
  if (res.status === 204) return null;
  return res.json();
}

/**
 * Multipart upload (attachments, files). The browser sets the multipart
 * boundary, so no Content-Type header is sent explicitly.
 *
 * @param {string} path
 * @param {Object} options
 * @param {string|null} options.token
 * @param {FormData} options.formData
 */
export async function apiUpload(path, { token, formData, method = "POST", signal } = {}) {
  const res = await doFetch(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(token),
    body: formData,
    signal,
  });
  await throwForStatus(res);
  if (res.status === 204) return null;
  return res.json();
}

function filenameFrom(disposition) {
  if (!disposition) return null;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      /* fall through to the plain name */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1] : null;
}

/**
 * Binary download (attachment / file content). Resolves to the blob plus
 * the file name and type the backend put on the response.
 */
export async function apiFetchBlob(path, { token, signal } = {}) {
  const res = await doFetch(`${API_BASE}${path}`, { headers: authHeaders(token), signal });
  await throwForStatus(res);
  return {
    blob: await res.blob(),
    contentType: res.headers.get("Content-Type") || "application/octet-stream",
    filename: filenameFrom(res.headers.get("Content-Disposition")),
  };
}

/** Build a query string, skipping blank/undefined values. */
export function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}
