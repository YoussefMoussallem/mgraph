/**
 * Langfuse observability pass-through — TS twin of Python
 * `tests/test_observability.py`.
 *
 * Request identity (userId / sessionId / traceMetadata / traceTags) must
 * reach the tracing helpers on every call shape, and the final observation
 * update must carry the tool-call summary, the cache token counters, and —
 * when the app injects a `costFn` pricer — the cost breakdown.
 *
 * `@genai-sdk/langfuse-client` is mocked wholesale — the TS equivalent of
 * Python's per-module `langfuse_generation` monkeypatch (ES module bindings
 * are immutable, so the seam is the module itself). The capture fake records
 * the attrs each factory call received plus every `update()` payload, like
 * the Python `_capture`/`_RecordingCtx` pair.
 */

import { beforeEach, expect, it, vi } from 'vitest';
import type OpenAI from 'openai';

type AnyRecord = Record<string, any>;

/** Shape of the mocked `generation`/`span` factories (handle-or-null). */
type LangfuseFactory = (
  name: string,
  model: string,
  inputData?: Record<string, unknown>,
  attrs?: Record<string, unknown>,
) => { update(data: AnyRecord): void; end(): void } | null;

const mockLangfuse = vi.hoisted(() => ({
  generation: (() => null) as LangfuseFactory,
  span: (() => null) as LangfuseFactory,
}));

vi.mock('@genai-sdk/langfuse-client', () => ({
  generation: (
    name: string,
    model: string,
    inputData?: Record<string, unknown>,
    attrs?: Record<string, unknown>,
  ) => mockLangfuse.generation(name, model, inputData, attrs),
  span: (
    name: string,
    model: string,
    inputData?: Record<string, unknown>,
    attrs?: Record<string, unknown>,
  ) => mockLangfuse.span(name, model, inputData, attrs),
}));

import { LLMAdapter } from '../src/adapter/core.js';
import type { ChatRequest, StreamEvent, SystemBlock } from '../src/schemas.js';

/**
 * The four identity attrs exactly as the adapters must forward them —
 * camelCase keys per the TS API surface (the Python twin asserts the
 * snake_case originals).
 */
const IDENTITY = {
  userId: 'user-42',
  sessionId: 'sess-7',
  metadata: { app: 'edwin' },
  tags: ['prod'],
};

interface CaptureStore {
  name?: string;
  attrs?: Record<string, unknown>;
  /** Every payload passed to the observation's `update()`, in order. */
  updates: AnyRecord[];
}

/** Stand-in for langfuse generation()/span() recording attrs + updates. */
function capture(store: CaptureStore): LangfuseFactory {
  return (name, _model, _inputData, attrs) => {
    store.name = name;
    store.attrs = attrs;
    return {
      update: (data: AnyRecord) => {
        store.updates.push(data);
      },
      end: () => {},
    };
  };
}

function identifiedRequest(extra: Partial<ChatRequest> = {}): ChatRequest {
  return {
    model: 'm',
    messages: [{ role: 'user', content: 'q' }],
    userId: 'user-42',
    sessionId: 'sess-7',
    traceMetadata: { app: 'edwin' },
    traceTags: ['prod'],
    ...extra,
  };
}

/** Constructor client injection — the TS seam replacing Python's monkeypatched `adapter.client`. */
function adapterWith(client: AnyRecord, costFn?: LLMAdapter['costFn']): LLMAdapter {
  return new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: client as unknown as OpenAI,
    costFn,
  });
}

function responsesAdapter(events: AnyRecord[], costFn?: LLMAdapter['costFn']): LLMAdapter {
  return adapterWith(
    {
      responses: {
        create: async () =>
          (async function* () {
            for (const e of events) {
              yield e;
            }
          })(),
      },
    },
    costFn,
  );
}

function chatAdapter(chunks: AnyRecord[], costFn?: LLMAdapter['costFn']): LLMAdapter {
  return adapterWith(
    {
      chat: {
        completions: {
          create: async () =>
            (async function* () {
              for (const c of chunks) {
                yield c;
              }
            })(),
        },
      },
    },
    costFn,
  );
}

function pricer(_model: string, usage: Record<string, unknown>) {
  return {
    input: Number(usage['input'] ?? 0) * 2e-6,
    output: Number(usage['output'] ?? 0) * 1e-5,
    cache_read_input_tokens: Number(usage['cache_read_input_tokens'] ?? 0) * 2e-7,
  };
}

async function collect(gen: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of gen) {
    events.push(ev);
  }
  return events;
}

beforeEach(() => {
  // Tracing off unless a test installs a capture — adapters must treat the
  // null return as no-op tracing.
  mockLangfuse.generation = () => null;
  mockLangfuse.span = () => null;
});

it('responses stream passes identity and records tools', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const usage = {
    input_tokens: 10,
    output_tokens: 5,
    input_tokens_details: { cached_tokens: 4 },
    cache_creation_input_tokens: 2,
  };
  const events = [
    {
      type: 'response.output_item.added',
      item: { type: 'function_call', id: 'i1', call_id: 'call_1', name: 'MakeSlide' },
    },
    { type: 'response.function_call_arguments.done', item_id: 'i1', arguments: '{}' },
    { type: 'response.completed', response: { usage, output: [] } },
  ];
  const adapter = responsesAdapter(events);

  await collect(adapter.stream(identifiedRequest(), 'sys'));

  expect(store.attrs).toEqual(IDENTITY);
  const update = store.updates.at(-1)!;
  expect(update['metadata']['tool_calls']).toEqual([{ call_id: 'call_1', name: 'MakeSlide' }]);
  expect(update['usageDetails']['cache_read_input_tokens']).toBe(4);
  expect(update['usageDetails']['cache_creation_input_tokens']).toBe(2);
});

it('responses stream without identity passes no attrs', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = responsesAdapter([]);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await collect(adapter.stream(req, 'sys'));

  expect(store.attrs).toEqual({});
  // No tool calls -> no metadata key on the final update.
  expect('metadata' in store.updates.at(-1)!).toBe(false);
});

it('chat stream passes identity and records tools', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const usage = {
    prompt_tokens: 100,
    completion_tokens: 20,
    prompt_tokens_details: { cached_tokens: 80 },
    cache_creation_input_tokens: 15,
  };
  const chunks = [
    {
      choices: [
        {
          delta: {
            content: null,
            reasoning_content: null,
            tool_calls: [{ index: 0, id: 'c1', function: { name: 'ListSlides', arguments: '{}' } }],
          },
          finish_reason: 'tool_calls',
        },
      ],
      usage: null,
    },
    { choices: [], usage },
  ];
  const adapter = chatAdapter(chunks);
  // Cache-flagged blocks route the request to the chat.completions path.
  const system: SystemBlock[] = [
    { text: 'S', cache: true },
    { text: 't' },
  ];

  await collect(adapter.stream(identifiedRequest(), system));

  expect(store.attrs).toEqual(IDENTITY);
  const update = store.updates.at(-1)!;
  expect(update['metadata']['tool_calls']).toEqual([{ call_id: 'c1', name: 'ListSlides' }]);
  expect(update['usageDetails']['cache_read_input_tokens']).toBe(80);
  expect(update['usageDetails']['cache_creation_input_tokens']).toBe(15);
});

it('complete passes identity to span', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.span = capture(store);
  const adapter = responsesAdapter([]);

  await collect(adapter.complete(identifiedRequest(), 'sys'));

  expect(store.attrs).toEqual(IDENTITY);
});

it('generate passes identity', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  // Non-streaming client shape: `responses.create` resolves to a plain
  // response object.
  const adapter = adapterWith({
    responses: { create: async () => ({ output: [], usage: null }) },
  });

  await adapter.generate(identifiedRequest(), 'sys');

  expect(store.attrs).toEqual(IDENTITY);
});

it('generate chat completion identity passthrough', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = adapterWith({
    chat: {
      completions: {
        create: async () => ({ choices: [{ message: { content: 't' } }], usage: null }),
      },
    },
  });

  await adapter.generateChatCompletion({
    model: 'm',
    systemPrompt: 's',
    userContent: 'u',
    userId: 'user-42',
    sessionId: 'sess-7',
    traceMetadata: { app: 'edwin' },
    traceTags: ['prod'],
  });

  expect(store.attrs).toEqual(IDENTITY);
});

it('generate chat completion forwards raw usage values to the observation', async () => {
  // Python passes these RAW (getattr with a 0 default only for ABSENT
  // fields, no as_int) — unlike the returnUsage path, which normalises on
  // both sides. A proxy's string/float usage must reach the observation
  // verbatim so both SDKs record identical usage_details.
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = adapterWith({
    chat: {
      completions: {
        create: async () => ({
          choices: [{ message: { content: 't' } }],
          usage: { prompt_tokens: '812', completion_tokens: 12.7 },
        }),
      },
    },
  });

  await adapter.generateChatCompletion({ model: 'm', systemPrompt: 's', userContent: 'u' });

  expect(store.updates.at(-1)!['usageDetails']).toStrictEqual({
    input: '812',
    output: 12.7,
  });
});

it('generate forwards present-null usage fields to the observation', async () => {
  // Python: getattr(usage, "input_tokens", 0) — 0 only when the field is
  // ABSENT; a present null forwards as null (Langfuse treats it as unset)
  // rather than asserting a zero token count.
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = adapterWith({
    responses: {
      create: async () => ({ output: [], usage: { input_tokens: null, output_tokens: 850 } }),
    },
  });

  await adapter.generate(identifiedRequest(), 'sys');

  expect(store.updates.at(-1)!['usageDetails']).toStrictEqual({
    input: null,
    output: 850,
  });
});

it('generate image identity passthrough', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = adapterWith({
    images: { generate: async () => ({ data: [], usage: null }) },
  });

  await adapter.generateImage({
    model: 'm',
    prompt: 'p',
    userId: 'user-42',
    sessionId: 'sess-7',
    traceMetadata: { app: 'edwin' },
    traceTags: ['prod'],
  });

  expect(store.attrs).toEqual(IDENTITY);
});

it('responses stream reports costDetails', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const usage = {
    input_tokens: 1000,
    output_tokens: 100,
    input_tokens_details: { cached_tokens: 400 },
    cache_creation_input_tokens: 0,
  };
  const events = [{ type: 'response.completed', response: { usage, output: [] } }];
  const adapter = responsesAdapter(events, pricer);

  await collect(adapter.stream(identifiedRequest(), 'sys'));

  const costs = store.updates.at(-1)!['costDetails'] as Record<string, number>;
  expect(costs['input']).toBeCloseTo(1000 * 2e-6);
  expect(costs['output']).toBeCloseTo(100 * 1e-5);
  expect(costs['cache_read_input_tokens']).toBeCloseTo(400 * 2e-7);
});

it('chat stream reports costDetails', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const usage = { prompt_tokens: 500, completion_tokens: 50 };
  const chunks = [{ choices: [], usage }];
  const system: SystemBlock[] = [{ text: 'S', cache: true }];
  const adapter = chatAdapter(chunks, pricer);

  await collect(adapter.stream(identifiedRequest(), system));

  const costs = store.updates.at(-1)!['costDetails'] as Record<string, number>;
  expect(costs['input']).toBeCloseTo(500 * 2e-6);
  expect(costs['output']).toBeCloseTo(50 * 1e-5);
});

it('generate chat completion reports costDetails', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = adapterWith(
    {
      chat: {
        completions: {
          create: async () => ({
            choices: [{ message: { content: 't' } }],
            usage: { prompt_tokens: 10, completion_tokens: 2 },
          }),
        },
      },
    },
    pricer,
  );

  await adapter.generateChatCompletion({ model: 'm', systemPrompt: 's', userContent: 'u' });

  const costs = store.updates.at(-1)!['costDetails'] as Record<string, number>;
  expect(costs['input']).toBeCloseTo(10 * 2e-6);
  expect(costs['output']).toBeCloseTo(2 * 1e-5);
});

it('generate image reports flat cost from image count', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const flatPricer = (_model: string, usage: Record<string, unknown>) => ({
    images: Number(usage['images'] ?? 0) * 0.04,
  });
  const b64 = Buffer.from('png').toString('base64');
  const adapter = adapterWith(
    {
      images: {
        generate: async () => ({
          data: [{ b64_json: b64 }, { b64_json: b64 }],
          usage: null,
        }),
      },
    },
    flatPricer,
  );

  await adapter.generateImage({ model: 'dall-e-3', prompt: 'p', n: 2 });

  const update = store.updates.at(-1)!;
  expect(update['usageDetails']['images']).toBe(2);
  expect(update['costDetails']['images']).toBeCloseTo(0.08);
});

it('costFn failure never breaks the call', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const broken = () => {
    throw new Error('pricing table down');
  };
  const adapter = responsesAdapter([], broken);

  const events = await collect(adapter.stream(identifiedRequest(), 'sys'));

  expect(events.at(-1)?.event).toBe('done');
  expect('costDetails' in store.updates.at(-1)!).toBe(false);
});

it('no costFn omits costDetails', async () => {
  const store: CaptureStore = { updates: [] };
  mockLangfuse.generation = capture(store);
  const adapter = responsesAdapter([]);

  await collect(adapter.stream(identifiedRequest(), 'sys'));

  expect('costDetails' in store.updates.at(-1)!).toBe(false);
});
