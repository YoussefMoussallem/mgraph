/**
 * Shared policy and plumbing for the adapter package.
 *
 * Policy — how a system prompt normalises, which model families take
 * `cache_control` — plus the cross-path helpers every endpoint module uses:
 * usage/cache-token extraction and the OpenAI→provider-agnostic error
 * translation applied around every SDK call.
 */

import { APIConnectionError, APIConnectionTimeoutError, APIError, APIUserAbortError } from 'openai';

import {
  classifyStatusError,
  ProviderConnectionError,
  ProviderServerError,
  type ProviderError,
} from '../errors.js';
import type { ChatRequest, SystemBlock } from '../schemas.js';

/**
 * App-supplied pricer: `(model, usageDetails) -> costDetails` in USD.
 *
 * The SDK is deliberately pricing-agnostic behind the proxy — it only defines
 * this interface. The pricer receives the model id and the exact
 * `usageDetails` dict about to be reported to Langfuse, and returns a cost
 * dict mirroring those keys (plus optional `total`; Langfuse sums the keys
 * when it's omitted). Return `null`/`undefined` for models it can't price.
 */
export type CostFn = (
  model: string,
  usageDetails: Record<string, unknown>,
) => Record<string, number> | null | undefined;

/**
 * `costDetails` for a Langfuse update, or `undefined` when no pricer is
 * configured or it declined/failed.
 *
 * Never throws — a buggy app pricer must not take down the traced call (same
 * contract as the rest of the tracing plumbing).
 */
export function costDetails(
  costFn: CostFn | null | undefined,
  model: string,
  usageDetails: Record<string, unknown>,
): Record<string, number> | undefined {
  if (costFn == null) {
    return undefined;
  }
  try {
    return costFn(model, usageDetails) ?? undefined;
  } catch (err) {
    console.warn(`costFn failed for model ${model}`, err);
    return undefined;
  }
}

/**
 * Normalise a system prompt to a list of non-empty blocks.
 *
 * A plain string becomes one unflagged block (never cached); empty strings
 * and empty-text blocks are dropped (even when flagged `cache: true`).
 * Idempotent, so both the facade and the endpoint modules can call it.
 */
export function systemBlocks(system: string | SystemBlock[]): SystemBlock[] {
  if (typeof system === 'string') {
    return system ? [{ text: system }] : [];
  }
  return system.filter((b) => b.text);
}

/** True iff any block carries a cache breakpoint flag. `[]` → false. */
export function wantsCache(blocks: SystemBlock[]): boolean {
  return blocks.some((b) => b.cache ?? false);
}

/** Trace identity/metadata attributes accepted by the tracing helpers. */
export interface TraceAttributes {
  userId?: string;
  sessionId?: string;
  metadata?: Record<string, unknown>;
  tags?: string[];
}

/**
 * Identity/metadata attributes for the tracing helpers, unset values
 * omitted.
 *
 * Filtering matches Python truthiness: empty strings, empty objects and
 * empty arrays are omitted too, not just null/undefined. The "nothing
 * provided" case stays an empty object so endpoint modules can spread it
 * without special-casing.
 */
export function traceAttributes(
  options: {
    userId?: string | null;
    sessionId?: string | null;
    metadata?: Record<string, unknown> | null;
    tags?: string[] | null;
  } = {},
): TraceAttributes {
  const attrs: TraceAttributes = {};
  if (options.userId) {
    attrs.userId = options.userId;
  }
  if (options.sessionId) {
    attrs.sessionId = options.sessionId;
  }
  if (options.metadata && Object.keys(options.metadata).length > 0) {
    attrs.metadata = options.metadata;
  }
  if (options.tags && options.tags.length > 0) {
    attrs.tags = options.tags;
  }
  return attrs;
}

/** Trace attributes carried on the request envelope. */
export function requestTraceAttributes(request: ChatRequest): TraceAttributes {
  return traceAttributes({
    userId: request.userId,
    sessionId: request.sessionId,
    metadata: request.traceMetadata,
    tags: request.traceTags,
  });
}

const CACHE_CONTROL_FAMILIES = ['claude', 'anthropic', 'gemini', 'vertex'] as const;

/**
 * True for backends where LiteLLM forwards Anthropic-style `cache_control`
 * breakpoints — Anthropic-native, Bedrock Claude, AND Gemini/Vertex (LiteLLM
 * maps the breakpoint onto Gemini context caching, so dropping it there
 * DISABLES caching).
 *
 * OpenAI (gpt / o-series) is the lone exception: it caches automatically by
 * prefix and rejects `cache_control` content blocks, so for it we strip the
 * markers and send plain text — its built-in prefix cache does the work,
 * keyed on the stable system prefix placed first. Allowlisting the
 * cache_control families keeps a never-seen model degrading to "no
 * breakpoint" (suboptimal) rather than "rejected request" (broken).
 * Case-insensitive substring match; null/empty-safe → false.
 */
export function supportsCacheControl(model: string | null | undefined): boolean {
  const m = (model ?? '').toLowerCase();
  return CACHE_CONTROL_FAMILIES.some((fam) => m.includes(fam));
}

/**
 * Map an OpenAI SDK error into the provider-agnostic hierarchy, or return
 * `null` when the error is not an SDK error (caller rethrows the original).
 *
 * Clause order matters: `APIUserAbortError` subclasses `APIError` (with an
 * `undefined` status) and must pass through UNTRANSLATED — an abort is
 * caller-initiated, and wrapping it as a provider failure would make
 * cancellation indistinguishable from an outage. `APIConnectionTimeoutError`
 * subclasses `APIConnectionError`, so the timeout check must come before it
 * to keep its clearer message. The original SDK error is always chained via
 * `cause`.
 *
 * A statusless `APIError` — the shape the SDK raises for a mid-stream SSE
 * `{"error": ...}` payload (a proxy/upstream failure after the HTTP 200) —
 * is translated to {@link ProviderServerError} so it never leaks a raw SDK
 * type to consumers and stays classified as a retryable provider-side
 * failure.
 */
export function toProviderError(err: unknown): ProviderError | null {
  if (err instanceof APIUserAbortError) {
    return null;
  }
  if (err instanceof APIError && typeof err.status === 'number') {
    return classifyStatusError(err.status, err.message, err);
  }
  if (err instanceof APIConnectionTimeoutError) {
    return new ProviderConnectionError('Request timed out', { cause: err });
  }
  if (err instanceof APIConnectionError) {
    // `err.message`, not `String(err)`: Python's `str(e)` yields just the
    // message ("Connection error."), while `String(err)` would prepend the
    // SDK class name — leaking the very type the hierarchy exists to hide
    // and breaking cross-SDK message parity.
    return new ProviderConnectionError(err.message, { cause: err });
  }
  if (err instanceof APIError) {
    return new ProviderServerError(err.message, { cause: err });
  }
  return null;
}

/**
 * Normalise a Chat Completions `finish_reason` into the cross-SDK
 * `stop_reason` vocabulary carried on the terminal `done` event.
 *
 * `stop` → `end_turn`, `length` → `max_tokens`, `tool_calls` /
 * `function_call` → `tool_use`; anything else (e.g. `content_filter`) is
 * forwarded verbatim rather than dropped, so consumers see the provider's
 * reason instead of an unexplained `null`. Absent/empty → `null` — the
 * "no terminal signal seen" marker consumers must treat as completion
 * unconfirmed.
 */
export function mapFinishReason(reason: string | null | undefined): string | null {
  if (!reason) {
    return null;
  }
  switch (reason) {
    case 'stop':
      return 'end_turn';
    case 'length':
      return 'max_tokens';
    case 'tool_calls':
    case 'function_call':
      return 'tool_use';
    default:
      return reason;
  }
}

/**
 * Run an SDK call, translating OpenAI SDK errors into the provider-agnostic
 * hierarchy via {@link toProviderError}.
 *
 * Every endpoint module wraps its non-streaming SDK calls in this so
 * application code never sees `openai` error types; streaming paths apply
 * {@link toProviderError} in their own catch blocks around SDK iteration.
 * Non-SDK errors pass through untranslated.
 */
export async function translateProviderErrors<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    throw toProviderError(err) ?? err;
  }
}

/**
 * Coerce a possibly-missing / non-numeric usage field to an integer
 * (0 on failure — null, undefined, NaN, non-integer strings, objects).
 * Numbers truncate toward zero, matching Python `int()`.
 */
export function asInt(value: unknown): number {
  if (!value) {
    return 0;
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? Math.trunc(value) : 0;
  }
  if (typeof value === 'string') {
    return /^[+-]?\d+$/.test(value.trim()) ? parseInt(value, 10) : 0;
  }
  return 0;
}

/**
 * Best-effort `[cacheRead, cacheWrite]` token counts from a provider usage
 * object.
 *
 * Spans both call shapes and the LiteLLM→Anthropic passthrough:
 *
 * - reads — input served from a cache breakpoint. Exposed OpenAI-style as
 *   `input_tokens_details.cached_tokens` / `prompt_tokens_details.cached_tokens`
 *   (checked in that order; a zero falls through to the next source), or as
 *   LiteLLM's top-level `cache_read_input_tokens` extra (last resort).
 * - writes — input written into the cache this call. Anthropic-only;
 *   surfaced by LiteLLM as `cache_creation_input_tokens`.
 *
 * Tolerant of any usage shape — typed SDK objects and plain records read the
 * same through property access (Python needed a separate `model_extra`
 * fallback; JS does not). Any absent field reads as 0, so a non-caching
 * model or provider simply reports no cache usage — the "for all models"
 * safe default. `null`/`undefined` usage → `[0, 0]`.
 */
export function extractCacheTokens(usage: unknown): [number, number] {
  if (usage == null) {
    return [0, 0];
  }
  const u = usage as Record<string, any>;

  let read = 0;
  for (const detailsKey of ['input_tokens_details', 'prompt_tokens_details'] as const) {
    const details = u[detailsKey];
    if (details == null) {
      continue;
    }
    read = read || asInt(details.cached_tokens);
  }
  read = read || asInt(u.cache_read_input_tokens);

  const write = asInt(u.cache_creation_input_tokens);
  return [read, write];
}
