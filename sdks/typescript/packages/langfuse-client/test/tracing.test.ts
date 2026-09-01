/**
 * `generation` / `span` helper contract — TS twin of
 * langfuse_client/tests/test_tracing.py.
 *
 * Two invariants callers rely on: the helpers return null whenever tracing
 * is off (guarded with `if (obs)` everywhere), and they NEVER throw — a
 * Langfuse/OTel failure must not take down the LLM call being traced.
 */

import { SpanStatusCode } from "@opentelemetry/api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------- SDK fakes
// Mirrors the Python fixtures: `startObservation` and `propagateAttributes`
// are the injection seams (Python monkeypatched the client's
// start_as_current_observation and tracing.propagate_attributes).

const mocks = vi.hoisted(() => ({
  startObservation: vi.fn(),
  propagateAttributes: vi.fn(),
}));

vi.mock("@langfuse/tracing", () => ({
  startObservation: mocks.startObservation,
  propagateAttributes: mocks.propagateAttributes,
  setLangfuseTracerProvider: vi.fn(),
}));

vi.mock("@langfuse/otel", () => ({
  LangfuseSpanProcessor: class {
    async forceFlush(): Promise<void> {}
    async shutdown(): Promise<void> {}
  },
}));

vi.mock("@opentelemetry/sdk-trace-node", () => ({
  NodeTracerProvider: class {
    async shutdown(): Promise<void> {}
    register(): void {}
  },
}));

vi.mock("@langfuse/client", () => ({
  LangfuseClient: class {},
}));

import { _resetForTests, initClient } from "../src/client.js";
import { generation, span } from "../src/index.js";

interface FakeObs {
  update: ReturnType<typeof vi.fn>;
  end: ReturnType<typeof vi.fn>;
}

function makeFakeObs(): FakeObs {
  return { update: vi.fn(), end: vi.fn() };
}

/** Fake initialised client (no network, all SDK constructors mocked). */
function initTracing(): void {
  initClient({ publicKey: "pk", secretKey: "sk" });
}

let warnSpy: ReturnType<typeof vi.spyOn>;
let debugSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  _resetForTests();
  mocks.startObservation.mockReset();
  mocks.propagateAttributes.mockReset();
  mocks.startObservation.mockImplementation(() => makeFakeObs());
  mocks.propagateAttributes.mockImplementation((_attrs: unknown, fn: () => unknown) =>
    fn(),
  );
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
});

afterEach(() => {
  warnSpy.mockRestore();
  debugSpy.mockRestore();
});

// ------------------------------------------------------------ core contract

it("helpers no-op without client", () => {
  expect(generation("g", "model-x")).toBeNull();
  expect(span("s", "model-x")).toBeNull();
  expect(mocks.startObservation).not.toHaveBeenCalled();
});

it("generation passes model first-class", () => {
  initTracing();
  const obs = makeFakeObs();
  mocks.startObservation.mockReturnValue(obs);

  const handle = generation("g", "model-x", { a: 1 });

  expect(handle).not.toBeNull();
  expect(handle!.observation).toBe(obs);
  expect(mocks.startObservation).toHaveBeenCalledExactlyOnceWith(
    "g",
    { model: "model-x", input: { a: 1 } },
    { asType: "generation" },
  );
});

it("span folds model into input", () => {
  // The span observation type has no first-class model field.
  initTracing();
  const obs = makeFakeObs();
  mocks.startObservation.mockReturnValue(obs);

  const handle = span("s", "model-x", { a: 1 });

  expect(handle).not.toBeNull();
  expect(handle!.observation).toBe(obs);
  expect(mocks.startObservation).toHaveBeenCalledExactlyOnceWith(
    "s",
    { input: { model: "model-x", a: 1 } },
    { asType: "span" },
  );
});

it("helpers swallow Langfuse errors", () => {
  initTracing();
  mocks.startObservation.mockImplementation(() => {
    throw new Error("otel exploded");
  });

  expect(generation("g", "m")).toBeNull();
  expect(span("s", "m")).toBeNull();
  expect(warnSpy).toHaveBeenCalledWith(
    expect.stringContaining("generation observation failed"),
    expect.anything(),
  );
  expect(warnSpy).toHaveBeenCalledWith(
    expect.stringContaining("span observation failed"),
    expect.anything(),
  );
});

// ------------------------------------------------- trace-attribute wrapper

describe("trace attributes", () => {
  it("identity propagates to trace attributes around observation start", () => {
    initTracing();
    const obs = makeFakeObs();
    const order: string[] = [];
    let capturedAttrs: unknown;
    mocks.startObservation.mockImplementation(() => {
      order.push("start");
      return obs;
    });
    mocks.propagateAttributes.mockImplementation(
      (attrs: unknown, fn: () => unknown) => {
        capturedAttrs = attrs;
        order.push("propagate:enter");
        const result = fn();
        order.push("propagate:exit");
        return result;
      },
    );

    const handle = generation("g", "m", undefined, {
      userId: "u1",
      sessionId: "s1",
      tags: ["t"],
    });

    expect(handle).not.toBeNull();
    expect(handle!.observation).toBe(obs);
    // Attributes filtered to exactly what was provided (metadata absent) and
    // the observation created INSIDE the propagate scope, so the processor
    // stamps them onto it.
    expect(capturedAttrs).toEqual({ userId: "u1", sessionId: "s1", tags: ["t"] });
    expect(Object.keys(capturedAttrs as object).sort()).toEqual([
      "sessionId",
      "tags",
      "userId",
    ]);
    expect(order).toEqual(["propagate:enter", "start", "propagate:exit"]);
  });

  it("no identity keeps the bare observation — zero added indirection", () => {
    initTracing();
    const obs = makeFakeObs();
    mocks.startObservation.mockReturnValue(obs);

    const handle = generation("g", "m");

    expect(mocks.propagateAttributes).not.toHaveBeenCalled();
    expect(handle!.observation).toBe(obs); // the exact SDK object, unwrapped
  });

  it("empty-valued identity counts as absent (Python truthiness parity)", () => {
    initTracing();
    generation("g", "m", undefined, {
      userId: "",
      sessionId: "",
      metadata: {},
      tags: [],
    });
    expect(mocks.propagateAttributes).not.toHaveBeenCalled();
    expect(mocks.startObservation).toHaveBeenCalledOnce();
  });

  it("propagate failure degrades to an un-attributed observation", () => {
    initTracing();
    const obs = makeFakeObs();
    mocks.startObservation.mockReturnValue(obs);
    mocks.propagateAttributes.mockImplementation(() => {
      throw new Error("bad attribute");
    });

    const handle = generation("g", "m", undefined, { userId: "u1" });

    // The traced call proceeds regardless, with a real observation.
    expect(handle).not.toBeNull();
    expect(handle!.observation).toBe(obs);
    expect(mocks.startObservation).toHaveBeenCalledOnce();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("propagateAttributes failed"),
      expect.anything(),
    );

    handle!.end();
    expect(obs.end).toHaveBeenCalledOnce(); // still closed cleanly
  });
});

// ------------------------------------------------- metadata normalization

describe('metadata normalization', () => {
  it('non-string values become JSON', () => {
    initTracing();
    let capturedAttrs: unknown;
    mocks.propagateAttributes.mockImplementation((attrs: unknown, fn: () => unknown) => {
      capturedAttrs = attrs;
      return fn();
    });

    generation('g', 'm', undefined, {
      metadata: { tenant: 'acme', flags: { beta: true }, attempt: 3 },
    });

    expect((capturedAttrs as { metadata: Record<string, string> }).metadata).toEqual({
      tenant: 'acme',
      flags: '{"beta":true}',
      attempt: '3',
    });
  });

  it('unserialisable value falls back to String()', () => {
    initTracing();
    let capturedAttrs: unknown;
    mocks.propagateAttributes.mockImplementation((attrs: unknown, fn: () => unknown) => {
      capturedAttrs = attrs;
      return fn();
    });
    const loop: Record<string, unknown> = {};
    loop['self'] = loop;

    generation('g', 'm', undefined, { metadata: { weird: loop } });

    expect((capturedAttrs as { metadata: Record<string, string> }).metadata.weird).toBe(
      String(loop),
    );
  });

  it('non-string keys are coerced', () => {
    initTracing();
    let capturedAttrs: unknown;
    mocks.propagateAttributes.mockImplementation((attrs: unknown, fn: () => unknown) => {
      capturedAttrs = attrs;
      return fn();
    });

    generation('g', 'm', undefined, { metadata: { 7: 'seven' } });

    expect((capturedAttrs as { metadata: Record<string, string> }).metadata).toEqual({
      '7': 'seven',
    });
  });
});

// ------------------------------------------------------- observation handle
// TS-specific surface: Python exposed a context manager; the TS port exposes
// an explicit handle whose update/end never throw and whose Symbol.dispose
// aliases end() for `using` blocks.

describe("observation handle", () => {
  it("update forwards to the observation and never throws", () => {
    initTracing();
    const obs = makeFakeObs();
    mocks.startObservation.mockReturnValue(obs);

    const handle = generation("g", "m")!;
    handle.update({ output: "done", usageDetails: { input: 1, output: 2 } });
    expect(obs.update).toHaveBeenCalledExactlyOnceWith({
      output: "done",
      usageDetails: { input: 1, output: 2 },
    });

    obs.update.mockImplementation(() => {
      throw new Error("update exploded");
    });
    expect(() => handle.update({ output: "x" })).not.toThrow();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("update failed"),
      expect.anything(),
    );
  });

  it("end never throws", () => {
    initTracing();
    const obs = makeFakeObs();
    obs.end.mockImplementation(() => {
      throw new Error("end exploded");
    });
    mocks.startObservation.mockReturnValue(obs);

    const handle = span("s", "m")!;
    expect(() => handle.end()).not.toThrow();
    expect(debugSpy).toHaveBeenCalledWith(
      expect.stringContaining("end failed"),
      expect.anything(),
    );
  });

  it("Symbol.dispose ends the observation (using-block support)", () => {
    initTracing();
    const obs = makeFakeObs();
    mocks.startObservation.mockReturnValue(obs);

    const handle = generation("g", "m")!;
    handle[Symbol.dispose]();
    expect(obs.end).toHaveBeenCalledOnce();
  });

  it("end(error) records the exception and ERROR status on the otel span", () => {
    // Python parity: exiting the Langfuse context manager with exc_info
    // records the exception and sets ERROR status via OTel's use_span. The
    // TS handle reaches the same span through `otelSpan`.
    initTracing();
    const otelSpan = { recordException: vi.fn(), setStatus: vi.fn() };
    const obs = { ...makeFakeObs(), otelSpan };
    mocks.startObservation.mockReturnValue(obs);

    const failure = new Error("boom");
    const handle = generation("g", "m")!;
    handle.end(failure);

    expect(otelSpan.recordException).toHaveBeenCalledExactlyOnceWith(failure);
    expect(otelSpan.setStatus).toHaveBeenCalledExactlyOnceWith({
      code: SpanStatusCode.ERROR,
      message: "Error: boom", // Python: f"{type(exc).__name__}: {exc}"
    });
    expect(obs.end).toHaveBeenCalledOnce();
  });

  it("end() without error closes clean — no exception, no error status", () => {
    initTracing();
    const otelSpan = { recordException: vi.fn(), setStatus: vi.fn() };
    const obs = { ...makeFakeObs(), otelSpan };
    mocks.startObservation.mockReturnValue(obs);

    const handle = span("s", "m")!;
    handle.end();

    expect(otelSpan.recordException).not.toHaveBeenCalled();
    expect(otelSpan.setStatus).not.toHaveBeenCalled();
    expect(obs.end).toHaveBeenCalledOnce();
  });

  it("end(error) still ends and never throws when error recording fails", () => {
    initTracing();
    const otelSpan = {
      recordException: vi.fn(() => {
        throw new Error("otel exploded");
      }),
      setStatus: vi.fn(),
    };
    const obs = { ...makeFakeObs(), otelSpan };
    mocks.startObservation.mockReturnValue(obs);

    const handle = generation("g", "m")!;
    expect(() => handle.end(new Error("boom"))).not.toThrow();
    expect(obs.end).toHaveBeenCalledOnce();

    // An observation without an otelSpan (defensive) also closes fine.
    const bare = makeFakeObs();
    mocks.startObservation.mockReturnValue(bare);
    const handle2 = generation("g", "m")!;
    expect(() => handle2.end("string failure")).not.toThrow();
    expect(bare.end).toHaveBeenCalledOnce();
  });
});
