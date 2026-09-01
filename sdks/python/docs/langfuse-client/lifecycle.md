# Lifecycle & guarantees

## The states

```
uninitialised ──init_client()──▶ active ──shutdown()──▶ terminated
      │                            │
      └── helpers no-op            └── helpers trace
```

- **Uninitialised** — `get_client()` returns `None`; `generation()` /
  `span()` return `None`; `flush()` / `shutdown()` are safe no-ops. Zero
  threads, zero network, zero log noise.
- **Active** — after `init_client()`. Adapter calls and helpers emit
  observations; spans export from a background batch thread.
- **Terminated** — after `shutdown()`. Back to no-op behaviour.

## `init_client` semantics

- **Validates eagerly** — empty `public_key`/`secret_key`, or a
  `cacert_path` that doesn't exist on disk, raise `ValueError` before any
  Langfuse state is touched.
- **Thread-safe** — concurrent first calls are serialized; exactly one
  client is constructed.
- **Idempotent per key** — calling again with the same `public_key` returns
  the existing client unchanged.
- **Refuses a second project** — a different `public_key` is *ignored with
  a warning* and the first client is kept. This is deliberate: registering
  a second Langfuse project in one process trips the upstream SDK's
  multi-project safety, after which bare client lookups return a *disabled*
  client — tracing dies silently. Refusing loudly beats disabling silently.

## `flush()` and `shutdown()`

Spans do not export inline — they queue into a background batch exporter.
Two consequences:

```python
from langfuse_client import flush, shutdown

flush()      # block until queued spans are exported
shutdown()   # flush + stop exporting; terminal for the process
```

- **Long-running services**: call `shutdown()` in the shutdown hook
  (FastAPI lifespan, SIGTERM handler) so the last batch isn't lost on
  deploy.
- **Short-lived processes** (batch jobs, scripts, one-off containers):
  `shutdown()` before exit is *required* in practice — without it, whatever
  is still in the exporter queue vanishes with the process.

Both are safe no-ops when tracing was never initialised, and both swallow
Langfuse errors (logged, never raised).

## The never-raise contract

`generation()` and `span()` return `None` in **all** failure modes: tracing
off, client shut down, or Langfuse/OTel throwing internally. Observability
must never take down the operation being observed. The one obligation this
puts on callers: **guard the context** —

```python
ctx = generation("step", model)
if ctx:
    with ctx as obs:
        ...
```

## Why the SDK tracks its own state

Upstream `langfuse.get_client()` never returns `None`. Called with nothing
initialised, it **constructs** a client on the fly — background exporter
threads, OpenTelemetry tracer state, warning logs — even with no
credentials. And in a process where two projects were ever registered, it
returns a disabled client rather than an error. This SDK therefore keeps
its own module-level handle: `get_client()` reflects exactly what
`init_client()` established, and "not initialised" stays a true, free no-op
state.
