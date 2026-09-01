/**
 * Langfuse observation helpers with a strict never-throw contract: they
 * return an observation handle when tracing is initialised, and null both
 * when it is not and whenever the Langfuse SDK throws — tracing must never
 * take down the LLM call being traced. Callers guard with `if (obs)` and
 * `end()` in `finally` (or `using`).
 *
 * Trace-level identity (userId, sessionId, metadata, tags) is applied via the
 * v5 `propagateAttributes` API, which must wrap the observation's creation so
 * the span processor stamps the attributes at span start. Attribute failures
 * degrade to an un-attributed observation — never to a broken call.
 */

import { SpanStatusCode } from "@opentelemetry/api";
import type { Span } from "@opentelemetry/api";
import { propagateAttributes, startObservation } from "@langfuse/tracing";
import type {
  LangfuseGeneration,
  LangfuseObservationAttributes,
  LangfuseSpan,
  PropagateAttributesParams,
} from "@langfuse/tracing";

import { getClient } from "./client.js";

/** The observation kinds these helpers create (both are updatable). */
export type TracedObservation = LangfuseGeneration | LangfuseSpan;

/**
 * Trace-level attributes propagated to the observation and its children —
 * what Langfuse aggregations (cost per user, session drill-down, tag
 * filters) key on. Empty strings/objects/arrays are treated as absent.
 */
export interface TraceAttrs {
  userId?: string;
  sessionId?: string;
  /** Free-form values; non-strings are normalized before propagation (see `normalizeMetadata`). */
  metadata?: Record<string, unknown>;
  tags?: string[];
}

/**
 * Handle around a Langfuse v5 observation. `update()` and `end()` never
 * throw (failures are logged); `end()` must be called exactly once when the
 * traced work finishes — `[Symbol.dispose]` aliases it so `using` works.
 */
export interface ObservationHandle {
  /** The raw Langfuse observation, for callers needing SDK-level access. */
  readonly observation: TracedObservation;
  /** Record output/usage/metadata on the observation; never throws. */
  update(data: LangfuseObservationAttributes): void;
  /**
   * End the observation; never throws. Pass the propagating error (if any)
   * so it is recorded on the underlying OTel span — an exception event plus
   * ERROR status — the TS equivalent of the Python SDK exiting the Langfuse
   * context manager with `sys.exc_info()`: failed calls must show up as
   * errored observations, not clean ones. Omit (or pass `undefined`/`null`)
   * for a clean close.
   */
  end(error?: unknown): void;
  [Symbol.dispose](): void;
}

/**
 * Fit a free-form metadata dict to `propagateAttributes`' contract.
 *
 * Langfuse coerces propagated metadata values with `String()` — a nested
 * object would land as `[object Object]` — and silently drops any coerced
 * value over 200 characters. Strings pass through untouched; everything else
 * is pre-serialised as compact JSON (`default=str` equivalent) so structure
 * at least survives as valid JSON text.
 */
function normalizeMetadata(
  metadata: Record<string, unknown> | undefined,
): Record<string, string> | undefined {
  if (!metadata || Object.keys(metadata).length === 0) {
    return undefined;
  }
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(metadata)) {
    const k = typeof key === 'string' ? key : String(key);
    if (typeof value === 'string') {
      out[k] = value;
    } else {
      try {
        out[k] = JSON.stringify(value, (_, v) =>
          typeof v === 'bigint' ? String(v) : v,
        );
      } catch {
        out[k] = String(value);
      }
    }
  }
  return out;
}

/**
 * Keep only truthy attributes (Python-truthiness parity: null, undefined,
 * "", {}, and [] all count as absent), in user/session/metadata/tags order.
 */
function filterAttrs(attrs?: TraceAttrs): PropagateAttributesParams | null {
  if (!attrs) {
    return null;
  }
  const out: PropagateAttributesParams = {};
  if (attrs.userId) {
    out.userId = attrs.userId;
  }
  if (attrs.sessionId) {
    out.sessionId = attrs.sessionId;
  }
  const metadata = normalizeMetadata(attrs.metadata as Record<string, unknown> | undefined);
  if (metadata && Object.keys(metadata).length > 0) {
    out.metadata = metadata;
  }
  if (attrs.tags && attrs.tags.length > 0) {
    out.tags = attrs.tags;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/**
 * Start an observation, wrapping creation in `propagateAttributes` when
 * trace attrs are present. Never throws:
 * - `start()` failing → warn ("... observation failed") and return null;
 * - `propagateAttributes` failing without having run `start()` → warn and
 *   retry un-attributed (degraded observation, exactly one extra attempt);
 * - `propagateAttributes` failing after `start()` ran → warn, keep the
 *   observation.
 */
function startObserved(
  kind: "generation" | "span",
  start: () => TracedObservation,
  attrs: PropagateAttributesParams | null,
): TracedObservation | null {
  let obs: TracedObservation | null = null;
  let started = false;
  let startError: unknown;
  const run = (): void => {
    try {
      obs = start();
      started = true;
    } catch (err) {
      startError = err;
    }
  };

  if (attrs !== null) {
    let propError: unknown;
    let propFailed = false;
    try {
      propagateAttributes(attrs, run);
    } catch (err) {
      propError = err;
      propFailed = true;
    }
    if (propFailed) {
      console.warn("Langfuse propagateAttributes failed", propError);
      if (!started && startError === undefined) {
        // propagateAttributes never invoked the callback — degrade to an
        // un-attributed observation rather than a broken call.
        run();
      }
    }
  } else {
    run();
  }

  if (!started) {
    console.warn(`Langfuse ${kind} observation failed`, startError);
    return null;
  }
  return obs;
}

function makeHandle(observation: TracedObservation): ObservationHandle {
  const end = (error?: unknown): void => {
    if (error !== undefined && error !== null) {
      // Mirror OTel's `use_span` exit behavior (what the Python SDK reaches
      // through the Langfuse context manager's `__exit__(*exc_info)`):
      // record the exception on the span and set ERROR status with a
      // "Name: message" description, then end as usual.
      try {
        const otelSpan = (observation as { otelSpan?: Span }).otelSpan;
        if (otelSpan) {
          otelSpan.recordException(
            error instanceof Error ? error : String(error),
          );
          otelSpan.setStatus({
            code: SpanStatusCode.ERROR,
            message:
              error instanceof Error
                ? `${error.name}: ${error.message}`
                : String(error),
          });
        }
      } catch (err) {
        console.debug("Langfuse observation error recording failed", err);
      }
    }
    try {
      observation.end();
    } catch (err) {
      console.debug("Langfuse observation end failed", err);
    }
  };
  return {
    observation,
    update(data: LangfuseObservationAttributes): void {
      try {
        observation.update(data);
      } catch (err) {
        console.warn("Langfuse observation update failed", err);
      }
    },
    end,
    [Symbol.dispose]: () => end(),
  };
}

/**
 * Langfuse *generation* observation handle, or null when tracing is off —
 * and a Langfuse failure degrades to null too (logged, never thrown).
 * `model` is a first-class generation field; `inputData` is passed through
 * verbatim.
 */
export function generation(
  name: string,
  model: string,
  inputData?: Record<string, unknown>,
  attrs?: TraceAttrs,
): ObservationHandle | null {
  if (getClient() === null) {
    return null;
  }
  const obs = startObserved(
    "generation",
    () => startObservation(name, { model, input: inputData }, { asType: "generation" }),
    filterAttrs(attrs),
  );
  return obs === null ? null : makeHandle(obs);
}

/**
 * Langfuse *span* observation handle, or null when tracing is off. `model`
 * is folded into the input payload (`{ model, ...inputData }`) — the span
 * observation type has no first-class model field, and an `inputData` key
 * named "model" overrides it. Same never-throw contract and trace-attribute
 * semantics as {@link generation}.
 */
export function span(
  name: string,
  model: string,
  inputData?: Record<string, unknown>,
  attrs?: TraceAttrs,
): ObservationHandle | null {
  if (getClient() === null) {
    return null;
  }
  const payload: Record<string, unknown> = { model, ...(inputData ?? {}) };
  const obs = startObserved(
    "span",
    () => startObservation(name, { input: payload }, { asType: "span" }),
    filterAttrs(attrs),
  );
  return obs === null ? null : makeHandle(obs);
}
