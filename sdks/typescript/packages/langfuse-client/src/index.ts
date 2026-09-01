/**
 * Langfuse client library — initialisation, lifecycle, and tracing helpers.
 * Public surface mirrors the Python `langfuse_client` package: idempotent
 * init, get-or-null client access, never-throw generation/span helpers, and
 * flush/shutdown lifecycle.
 */

export { flush, getClient, initClient, shutdown } from "./client.js";
export type { InitClientOptions } from "./client.js";
export { generation, span } from "./tracing.js";
export type { ObservationHandle, TraceAttrs, TracedObservation } from "./tracing.js";
