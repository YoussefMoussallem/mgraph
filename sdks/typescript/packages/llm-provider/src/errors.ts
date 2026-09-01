/**
 * Provider-agnostic error hierarchy for LLM calls.
 *
 * Callers catch against this hierarchy instead of the OpenAI SDK's own
 * error types so the rest of the codebase stays decoupled from the
 * underlying client. Every error carries the HTTP status so logs and
 * user-facing messages can differentiate transient issues (rate limits,
 * timeouts) from permanent ones (bad key, bad input).
 *
 * Hierarchy:
 *
 *     ProviderError
 *     ├─ ProviderAuthError           (401/403 — bad or missing API key)
 *     ├─ ProviderRateLimitError      (429 — quota exceeded)
 *     ├─ ProviderNotFoundError       (404 — invalid model name)
 *     ├─ ProviderInvalidRequestError (400 — malformed input)
 *     ├─ ProviderConnectionError     (network / timeout)
 *     └─ ProviderServerError         (5xx — provider-side failure)
 */

/** Construction options shared by every {@link ProviderError} class. */
export interface ProviderErrorOptions {
  /** HTTP status of the failed call, when one exists. Default `null`. */
  statusCode?: number | null;
  /** Original SDK error, chained via ES2022 `Error.cause` for debugging. */
  cause?: unknown;
}

/**
 * Base error for all LLM provider failures.
 *
 * Holds the HTTP status so downstream handlers can decide whether to
 * retry, surface the error to the user, or page oncall.
 */
export class ProviderError extends Error {
  statusCode: number | null;

  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, 'cause' in options ? { cause: options.cause } : undefined);
    this.name = 'ProviderError';
    this.statusCode = options.statusCode ?? null;
  }
}

/**
 * Invalid or missing API key (401/403).
 *
 * Usually a config error — do not retry; alert operators.
 */
export class ProviderAuthError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderAuthError';
  }
}

/**
 * Rate limit or quota exceeded (429).
 *
 * Typically safe to retry after backoff.
 */
export class ProviderRateLimitError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderRateLimitError';
  }
}

/**
 * Model or resource not found (404).
 *
 * Usually the model name is wrong or has been retired — do not retry.
 */
export class ProviderNotFoundError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderNotFoundError';
  }
}

/**
 * Bad request — malformed input or unsupported parameters (400).
 *
 * Retrying without changing the input will keep failing; surface to the
 * caller so the request can be fixed.
 */
export class ProviderInvalidRequestError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderInvalidRequestError';
  }
}

/**
 * Network connectivity or timeout error.
 *
 * Transient by nature — retry with backoff is usually appropriate.
 */
export class ProviderConnectionError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderConnectionError';
  }
}

/**
 * Provider-side server error (5xx).
 *
 * Likely transient; retry with backoff, but alert if it persists.
 */
export class ProviderServerError extends ProviderError {
  constructor(message: string, options: ProviderErrorOptions = {}) {
    super(message, options);
    this.name = 'ProviderServerError';
  }
}

/**
 * Map an HTTP status code to the appropriate {@link ProviderError} subclass.
 *
 * The adapter uses this to translate OpenAI SDK status errors into our own
 * hierarchy at the boundary, so application code never imports `openai`
 * error types or branches on integer status codes directly. Unmapped codes
 * fall back to the base {@link ProviderError} — this never throws. Every
 * result carries `statusCode`; `cause` (when given) chains the original SDK
 * error onto the result.
 */
export function classifyStatusError(
  statusCode: number,
  message: string,
  cause?: unknown,
): ProviderError {
  const options: ProviderErrorOptions =
    cause === undefined ? { statusCode } : { statusCode, cause };
  if (statusCode === 401 || statusCode === 403) {
    return new ProviderAuthError(message, options);
  }
  if (statusCode === 429) {
    return new ProviderRateLimitError(message, options);
  }
  if (statusCode === 404) {
    return new ProviderNotFoundError(message, options);
  }
  if (statusCode === 400) {
    return new ProviderInvalidRequestError(message, options);
  }
  if (statusCode >= 500) {
    return new ProviderServerError(message, options);
  }
  return new ProviderError(message, options);
}
