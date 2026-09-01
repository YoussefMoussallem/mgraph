/**
 * `initClient` lifecycle + corporate-proxy plumbing — TS twin of
 * langfuse_client/tests/test_init_client.py.
 *
 * Pins the edge-case contract: validation fails fast, init is idempotent (a
 * second project key is refused — it would trip Langfuse's multi-project
 * safety and silently disable tracing), `getClient` never constructs
 * implicitly, flush/shutdown are safe no-ops when uninitialised, and
 * `cacertPath`/`proxyToken` cover BOTH transports (client + OTLP-exporting
 * span processor headers, plus `OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE`,
 * deferring to deployment-set OTel vars).
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const OTEL_CERT_TRACES = "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE";
const OTEL_CERT_GENERAL = "OTEL_EXPORTER_OTLP_CERTIFICATE";

// ---------------------------------------------------------------- SDK fakes
// Mirrors the Python fixture stubbing `Langfuse` + `httpx.Client`: every
// construction is captured so tests can assert kwargs and instance counts;
// no network or real OTel state is touched.

const captured = vi.hoisted(() => ({
  processors: [] as any[],
  providers: [] as any[],
  clients: [] as any[],
  tracerProviderSets: [] as unknown[],
}));

vi.mock("@langfuse/otel", () => ({
  LangfuseSpanProcessor: class {
    params: any;
    flushed = 0;
    constructor(params?: any) {
      this.params = params ?? {};
      captured.processors.push(this);
    }
    async forceFlush(): Promise<void> {
      this.flushed += 1;
    }
    async shutdown(): Promise<void> {}
  },
}));

vi.mock("@opentelemetry/sdk-trace-node", () => ({
  NodeTracerProvider: class {
    config: any;
    shutDown = 0;
    registered = 0;
    constructor(config?: any) {
      this.config = config ?? {};
      captured.providers.push(this);
    }
    register(): void {
      this.registered += 1;
    }
    async shutdown(): Promise<void> {
      this.shutDown += 1;
    }
  },
}));

vi.mock("@langfuse/client", () => ({
  LangfuseClient: class {
    params: any;
    flushed = 0;
    shutDown = 0;
    constructor(params?: any) {
      this.params = params ?? {};
      captured.clients.push(this);
    }
    async flush(): Promise<void> {
      this.flushed += 1;
    }
    async shutdown(): Promise<void> {
      this.shutDown += 1;
    }
  },
}));

vi.mock("@langfuse/tracing", () => ({
  setLangfuseTracerProvider: (provider: unknown) => {
    captured.tracerProviderSets.push(provider);
  },
  startObservation: vi.fn(),
  propagateAttributes: vi.fn(),
}));

import { _resetForTests, initClient } from "../src/client.js";
import { flush, getClient, shutdown } from "../src/index.js";

// A CA bundle path that exists — cacertPath is validated as an existing file.
const tmp = mkdtempSync(join(tmpdir(), "genai-lfc-"));
const pem = join(tmp, "corp-ca.pem");
writeFileSync(pem, "dummy");

const savedTraces = process.env[OTEL_CERT_TRACES];
const savedGeneral = process.env[OTEL_CERT_GENERAL];

let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  _resetForTests();
  captured.processors.length = 0;
  captured.providers.length = 0;
  captured.clients.length = 0;
  captured.tracerProviderSets.length = 0;
  delete process.env[OTEL_CERT_TRACES];
  delete process.env[OTEL_CERT_GENERAL];
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  warnSpy.mockRestore();
  delete process.env[OTEL_CERT_TRACES];
  delete process.env[OTEL_CERT_GENERAL];
});

afterAll(() => {
  if (savedTraces !== undefined) process.env[OTEL_CERT_TRACES] = savedTraces;
  if (savedGeneral !== undefined) process.env[OTEL_CERT_GENERAL] = savedGeneral;
  rmSync(tmp, { recursive: true, force: true });
});

// --------------------------------------------------------------- happy paths

describe("happy paths", () => {
  it("plain init passes no headers and sets no env", () => {
    const client = initClient({ publicKey: "pk", secretKey: "sk" });

    expect(captured.processors).toHaveLength(1);
    expect(captured.processors[0].params.additionalHeaders).toBeUndefined();
    expect(captured.clients[0].params.additionalHeaders).toBeUndefined();
    expect(process.env[OTEL_CERT_TRACES]).toBeUndefined();
    expect(process.env[OTEL_CERT_GENERAL]).toBeUndefined();
    // Returns the client, and getClient() hands back the same one.
    expect(client).toBe(captured.clients[0]);
    expect(getClient()).toBe(client);
    // The dedicated provider carries the processor and is handed to
    // @langfuse/tracing — never registered as the OTel global.
    expect(captured.providers[0].config.spanProcessors).toEqual([
      captured.processors[0],
    ]);
    expect(captured.tracerProviderSets).toEqual([captured.providers[0]]);
    expect(captured.providers[0].registered).toBe(0);
  });

  it("cacert and token cover both transports", () => {
    initClient({
      publicKey: "pk",
      secretKey: "sk",
      cacertPath: pem,
      proxyToken: "Bearer t",
    });
    // REST path: the client receives the proxy header.
    expect(captured.clients[0].params.additionalHeaders["Proxy-Authorization"]).toBe(
      "Bearer t",
    );
    // OTLP path: trace-scoped env var for the exporter + merged headers on
    // the span processor.
    expect(process.env[OTEL_CERT_TRACES]).toBe(pem);
    expect(
      captured.processors[0].params.additionalHeaders["Proxy-Authorization"],
    ).toBe("Bearer t");
  });

  it("token without cacert still builds headers", () => {
    initClient({ publicKey: "pk", secretKey: "sk", proxyToken: "Bearer t" });
    expect(
      captured.processors[0].params.additionalHeaders["Proxy-Authorization"],
    ).toBe("Bearer t");
    expect(captured.clients[0].params.additionalHeaders["Proxy-Authorization"]).toBe(
      "Bearer t",
    );
    expect(process.env[OTEL_CERT_TRACES]).toBeUndefined();
  });

  it("base url trailing slash stripped", () => {
    initClient({ publicKey: "pk", secretKey: "sk", baseUrl: "https://lf.internal/" });
    expect(captured.processors[0].params.baseUrl).toBe("https://lf.internal");
    expect(captured.clients[0].params.baseUrl).toBe("https://lf.internal");
  });
});

// ---------------------------------------------------------------- validation

describe("validation", () => {
  it("empty credentials throw", () => {
    expect(() => initClient({ publicKey: "", secretKey: "sk" })).toThrow(Error);
    expect(() => initClient({ publicKey: "pk", secretKey: "" })).toThrow(Error);
    // Nothing constructed.
    expect(captured.clients).toHaveLength(0);
    expect(captured.processors).toHaveLength(0);
  });

  it("missing cacert file throws", () => {
    expect(() =>
      initClient({ publicKey: "pk", secretKey: "sk", cacertPath: "no/such.pem" }),
    ).toThrow(/cacertPath/);
    // A directory is not a file either.
    expect(() =>
      initClient({ publicKey: "pk", secretKey: "sk", cacertPath: tmp }),
    ).toThrow(/cacertPath/);
    expect(captured.clients).toHaveLength(0);
    expect(process.env[OTEL_CERT_TRACES]).toBeUndefined();
  });
});

// ------------------------------------------------------------------- re-init

describe("re-init", () => {
  it("reinit with same key is idempotent", () => {
    const first = initClient({ publicKey: "pk", secretKey: "sk" });
    const second = initClient({ publicKey: "pk", secretKey: "sk" });
    expect(second).toBe(first);
    expect(captured.clients).toHaveLength(1);
    expect(captured.processors).toHaveLength(1);
  });

  it("reinit with different key keeps first and warns", () => {
    const first = initClient({ publicKey: "pk-1", secretKey: "sk" });
    const second = initClient({ publicKey: "pk-2", secretKey: "sk" });
    expect(second).toBe(first);
    expect(captured.clients).toHaveLength(1);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("different public key"),
    );
  });
});

// ------------------------------------------------------- OTel var deference

describe("OTel cert var deference", () => {
  it("deployment traces var wins", () => {
    process.env[OTEL_CERT_TRACES] = "deployment.pem";
    initClient({ publicKey: "pk", secretKey: "sk", cacertPath: pem });
    expect(process.env[OTEL_CERT_TRACES]).toBe("deployment.pem");
  });

  it("deployment general var blocks the sdk traces var", () => {
    // The traces-specific var OUTRANKS the general one in the exporter, so
    // the SDK must not set it when the deployment configured the general var
    // — doing so would silently override platform OTel config.
    process.env[OTEL_CERT_GENERAL] = "platform.pem";
    initClient({ publicKey: "pk", secretKey: "sk", cacertPath: pem });
    expect(process.env[OTEL_CERT_TRACES]).toBeUndefined();
    expect(process.env[OTEL_CERT_GENERAL]).toBe("platform.pem");
  });
});

// ---------------------------------------------------------------- passthroughs

describe("header passthrough", () => {
  it("explicit Proxy-Authorization header wins over proxyToken", () => {
    initClient({
      publicKey: "pk",
      secretKey: "sk",
      additionalHeaders: { "Proxy-Authorization": "explicit" },
      proxyToken: "Bearer t",
    });
    expect(
      captured.processors[0].params.additionalHeaders["Proxy-Authorization"],
    ).toBe("explicit");
    expect(captured.clients[0].params.additionalHeaders["Proxy-Authorization"]).toBe(
      "explicit",
    );
  });

  it("caller headers never mutated; OTLP env still covered alongside them", () => {
    // TS twin of test_custom_httpx_client_is_passed_through: the TS API has
    // no transport-injection parameter (deliberate divergence, see
    // PARITY.md), so this pins the surviving halves of that contract — the
    // caller's header object is passed through by value, and the OTLP env
    // plumbing runs regardless of header configuration.
    const mine = { "X-Corp": "1" };
    initClient({
      publicKey: "pk",
      secretKey: "sk",
      additionalHeaders: mine,
      cacertPath: pem,
    });
    expect(mine).toEqual({ "X-Corp": "1" }); // caller's object untouched
    expect(captured.processors[0].params.additionalHeaders).toEqual({
      "X-Corp": "1",
    });
    expect(captured.processors[0].params.additionalHeaders).not.toBe(mine);
    // OTLP side is still covered.
    expect(process.env[OTEL_CERT_TRACES]).toBe(pem);
  });
});

// -------------------------------------------------------- flush / shutdown

describe("flush and shutdown", () => {
  it("are safe no-ops when uninitialised", async () => {
    await flush();
    await shutdown(); // neither throws nor rejects
    expect(getClient()).toBeNull();
  });

  it("lifecycle: flush flushes, shutdown is terminal and idempotent", async () => {
    const client = initClient({ publicKey: "pk", secretKey: "sk" });
    expect(getClient()).toBe(client);

    await flush();
    expect(captured.processors[0].flushed).toBe(1);
    // Python's client.flush() drains ALL client-owned queues; here the
    // LangfuseClient's own background queues (batched scores, media) must
    // be drained alongside the span pipeline — flushing only OTel spans
    // would silently lose queued score events on process exit.
    expect(captured.clients[0].flushed).toBe(1);

    await shutdown();
    expect(captured.providers[0].shutDown).toBe(1);
    // Same for shutdown: the LangfuseClient is shut down (which flushes its
    // queues) before the only reference to it is dropped.
    expect(captured.clients[0].shutDown).toBe(1);
    expect(getClient()).toBeNull(); // terminal: helpers no-op again
    // The tracing provider is detached again on shutdown.
    expect(captured.tracerProviderSets.at(-1)).toBeNull();

    await shutdown(); // second shutdown is a safe no-op
    expect(captured.providers[0].shutDown).toBe(1);
    expect(captured.clients[0].shutDown).toBe(1);
  });

  it("swallow Langfuse errors", async () => {
    initClient({ publicKey: "pk", secretKey: "sk" });
    captured.processors[0].forceFlush = () => {
      throw new Error("exporter died");
    };
    captured.providers[0].shutdown = () => {
      throw new Error("exporter died");
    };

    await flush(); // logged, not thrown
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("flush failed"),
      expect.anything(),
    );
    expect(getClient()).not.toBeNull(); // flush failure does not clear state

    await shutdown(); // logged, not thrown — and state still cleared
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("shutdown failed"),
      expect.anything(),
    );
    expect(getClient()).toBeNull();
  });
});
