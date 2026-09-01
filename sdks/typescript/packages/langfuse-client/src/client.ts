/**
 * Langfuse lifecycle state owned by THIS module, never read back from Langfuse
 * or OTel globals. The Langfuse JS SDK v5 traces through OpenTelemetry, and
 * registering it globally (or letting it self-construct) would leak background
 * exporters and make "not initialised" impossible to represent. Instead a
 * dedicated `NodeTracerProvider` carrying a `LangfuseSpanProcessor` is routed
 * to `@langfuse/tracing` via `setLangfuseTracerProvider` — the OTel global
 * provider is never touched — so uninitialised stays a real, cheap no-op state
 * and `getClient()` can honestly return null.
 */

import { existsSync, statSync } from "node:fs";

import { LangfuseClient } from "@langfuse/client";
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { setLangfuseTracerProvider } from "@langfuse/tracing";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";

const DEFAULT_BASE_URL = "https://cloud.langfuse.com";
const OTEL_CERT_TRACES = "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE";
const OTEL_CERT_GENERAL = "OTEL_EXPORTER_OTLP_CERTIFICATE";

/** Options for {@link initClient}. */
export interface InitClientOptions {
  /** Langfuse public API key. Required, non-empty. */
  publicKey: string;
  /** Langfuse secret API key. Required, non-empty. */
  secretKey: string;
  /** Langfuse instance URL; trailing slashes are stripped. */
  baseUrl?: string;
  /** Extra HTTP headers for both the REST client and the OTLP span exporter. */
  additionalHeaders?: Record<string, string>;
  /**
   * CA bundle for corporate TLS interception. Must be an existing file.
   * Covers the OTLP span exporter via `OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE`
   * (deployment-set OTel cert vars always win). Node-level trust for the REST
   * fetch path is documented in docs/langfuse-client/corporate-network.md.
   */
  cacertPath?: string;
  /**
   * Value for a `Proxy-Authorization` header on both transports. An explicit
   * `additionalHeaders["Proxy-Authorization"]` entry wins over this.
   */
  proxyToken?: string;
}

// Module state — the singleton and the tracing handles that flush/shutdown
// operate on. No lock: the init path is fully synchronous, so the
// check-then-set below is naturally atomic on the JS event loop.
let _client: LangfuseClient | null = null;
let _publicKey: string | null = null;
let _processor: LangfuseSpanProcessor | null = null;
let _provider: NodeTracerProvider | null = null;

/**
 * Initialise the process-wide Langfuse client and tracing pipeline, returning
 * the client handle.
 *
 * Idempotent per `publicKey`: repeat calls with the same key return the
 * existing client; a different key is ignored with a warning (a second
 * Langfuse project in one process would silently disable tracing).
 *
 * Note: construction succeeding does not prove the credentials are valid —
 * Langfuse validates lazily in the background.
 *
 * @throws Error on empty credentials or a `cacertPath` that is not an
 *   existing file. Validation runs before the idempotency check and before
 *   any state or environment mutation.
 */
export function initClient(options: InitClientOptions): LangfuseClient {
  const { publicKey, secretKey, additionalHeaders, cacertPath, proxyToken } =
    options;

  if (!publicKey || !secretKey) {
    throw new Error("initClient requires non-empty publicKey and secretKey");
  }
  if (cacertPath && !(existsSync(cacertPath) && statSync(cacertPath).isFile())) {
    throw new Error(`cacertPath does not exist: ${cacertPath}`);
  }

  if (_client !== null) {
    if (publicKey === _publicKey) {
      return _client;
    }
    console.warn(
      "langfuse-client already initialised with a different public key; " +
        "keeping the existing client (re-init ignored)",
    );
    return _client;
  }

  // Copy so the caller's object is never mutated; an explicit
  // Proxy-Authorization entry wins over proxyToken.
  const headers: Record<string, string> = { ...(additionalHeaders ?? {}) };
  if (proxyToken && !("Proxy-Authorization" in headers)) {
    headers["Proxy-Authorization"] = proxyToken;
  }
  const mergedHeaders = Object.keys(headers).length > 0 ? headers : undefined;

  if (
    cacertPath &&
    !process.env[OTEL_CERT_TRACES] &&
    !process.env[OTEL_CERT_GENERAL]
  ) {
    // Trace-scoped, so exporters for other signals are untouched; skipped
    // when the deployment configured either OTel cert var (the traces var
    // outranks the general one, so setting it unconditionally could override
    // a deployment that only set the general var).
    process.env[OTEL_CERT_TRACES] = cacertPath;
  }

  const baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");

  const processor = new LangfuseSpanProcessor({
    publicKey,
    secretKey,
    baseUrl,
    additionalHeaders: mergedHeaders,
  });
  const provider = new NodeTracerProvider({ spanProcessors: [processor] });
  // Dedicated provider for @langfuse/tracing only — deliberately NOT
  // registered as the OTel global (`provider.register()` is never called).
  setLangfuseTracerProvider(provider);

  _client = new LangfuseClient({
    publicKey,
    secretKey,
    baseUrl,
    additionalHeaders: mergedHeaders,
  });
  _publicKey = publicKey;
  _processor = processor;
  _provider = provider;
  return _client;
}

/** The client from {@link initClient}, or null. Never constructs implicitly. */
export function getClient(): LangfuseClient | null {
  return _client;
}

/**
 * Flush pending data to Langfuse; no-op when uninitialised, never throws or
 * rejects (failures are logged). Python's `client.flush()` drains every
 * queue the v3 client owns; here that work is split across two objects, so
 * both are drained: the span processor's export batch AND the
 * `LangfuseClient`'s own background queues (batched score ingestion, media
 * uploads). Call this before points where the process might exit.
 */
export async function flush(): Promise<void> {
  if (_client === null) {
    return;
  }
  try {
    await Promise.all([_processor?.forceFlush(), _client.flush()]);
  } catch (err) {
    console.warn("Langfuse flush failed", err);
  }
}

/**
 * Flush and permanently shut down tracing; no-op when uninitialised, never
 * throws or rejects. Terminal for the process: state is cleared even when the
 * underlying shutdown fails, so `getClient()` returns null and the tracing
 * helpers no-op afterwards.
 */
export async function shutdown(): Promise<void> {
  if (_client === null) {
    return;
  }
  try {
    // Python's `client.shutdown()` flushes and terminates every background
    // worker the v3 client owns. Both counterparts shut down here: the OTel
    // tracing pipeline (provider → span processor) AND the LangfuseClient's
    // own queues (batched scores, media uploads) — dropping the client
    // reference without draining it would silently lose queued events.
    await Promise.all([_provider?.shutdown(), _client.shutdown()]);
  } catch (err) {
    console.warn("Langfuse shutdown failed", err);
  } finally {
    try {
      setLangfuseTracerProvider(null);
    } catch {
      // never-throw contract
    }
    _client = null;
    _publicKey = null;
    _processor = null;
    _provider = null;
  }
}

/**
 * Test-only: reset module state without exporting spans. Not part of the
 * public API and not re-exported from the package index.
 */
export function _resetForTests(): void {
  try {
    setLangfuseTracerProvider(null);
  } catch {
    // ignore — tests may have stubbed the tracing module
  }
  _client = null;
  _publicKey = null;
  _processor = null;
  _provider = null;
}
