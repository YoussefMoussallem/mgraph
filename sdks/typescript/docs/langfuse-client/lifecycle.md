# Lifecycle & guarantees

## The states

```
uninitialised ──initClient()──▶ active ──shutdown()──▶ terminated
      │                           │
      └── helpers no-op           └── helpers trace
```

- **Uninitialised** — `getClient()` returns `null`; `generation()` /
  `span()` return `null`; `flush()` / `shutdown()` are safe no-ops. Zero
  background tasks, zero network, zero log noise.
- **Active** — after `initClient()`. Adapter calls and helpers emit
  observations; spans export from a background batch task.
- **Terminated** — after `shutdown()`. Back to no-op behaviour:
  `getClient()` returns `null` again and the helpers no-op.

## `initClient` semantics

- **Validates eagerly** — empty `publicKey`/`secretKey`, or a `cacertPath`
  that doesn't exist on disk, throw an `Error` before any Langfuse state or
  environment is touched.
- **Concurrency-safe** — the init path is fully synchronous, so the
  check-then-set is atomic on the JS event loop; exactly one client is
  constructed.
- **Idempotent per key** — calling again with the same `publicKey` returns
  the existing client unchanged.
- **Refuses a second project** — a different `publicKey` is *ignored with a
  warning* and the first client is kept. This is deliberate: registering a
  second Langfuse project in one process would silently disable tracing.
  Refusing loudly beats disabling silently.

## `flush()` and `shutdown()`

Spans do not export inline — they queue into a background batch exporter.
Two consequences:

```ts
import { flush, shutdown } from '@genai-sdk/langfuse-client';

await flush();     // resolve once queued spans are exported
await shutdown();  // flush + stop exporting; terminal for the process
```

- **Long-running services**: call `shutdown()` in the shutdown hook
  (SIGTERM handler, framework `onClose` hook) so the last batch isn't lost
  on deploy.
- **Short-lived processes** (batch jobs, scripts, one-off containers):
  `await shutdown()` before exit is *required* in practice — without it,
  whatever is still in the exporter queue vanishes with the process.

Both are safe no-ops when tracing was never initialised, and both swallow
Langfuse errors (logged, never thrown or rejected). `shutdown()` clears the
module state even when the underlying teardown fails.

## The never-throw contract

`generation()` and `span()` return `null` in **all** failure modes: tracing
off, client shut down, or Langfuse/OTel throwing internally. Observability
must never take down the operation being observed. The one obligation this
puts on callers: **guard the handle** —

```ts
const obs = generation('step', model);
if (obs) {
  try {
    // ... traced work, obs.update(...)
  } finally {
    obs.end();
  }
}
```

(`update()` and `end()` on the handle are themselves never-throw — failures
are logged.)

## Why the SDK tracks its own state

The Langfuse JS SDK v5 traces through OpenTelemetry. Registering it as the
OTel global provider — or letting it self-construct on first use — would
leak background exporters into every process and make "not initialised"
impossible to represent. This SDK therefore keeps its own module-level
state: `initClient()` registers a dedicated `NodeTracerProvider` carrying a
`LangfuseSpanProcessor` and routes it to `@langfuse/tracing` directly (the
OTel global provider is never touched, so your app's own OTel setup is
unaffected). `getClient()` reflects exactly what `initClient()` established,
and "not initialised" stays a true, free no-op state.
