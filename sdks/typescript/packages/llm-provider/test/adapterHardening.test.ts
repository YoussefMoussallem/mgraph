/**
 * Guards for the hardening fixes layered onto the extracted SDK — TS twin of
 * Python `tests/test_adapter_hardening.py`.
 *
 * Pins behaviour the edwin-embedded copy did not have: Langfuse observation
 * release when a consumer abandons a stream mid-flight, usage-less terminal
 * events, `generate()` honouring the output-token cap, `tool_call_start`
 * deferred until the call's identity is known, and the hosted-tool drop
 * warning on the chat path.
 *
 * Python asserts the observation context's `__exit__` receives GeneratorExit
 * on abandonment; the portable TS equivalent asserts `end()` ran from the
 * generator's teardown (finally) after an early `return()` — without the
 * clean-completion update that only a fully-consumed stream performs.
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

import { APIError } from 'openai';

import * as adapterChat from '../src/adapter/chatCompletions.js';
import { systemBlocks } from '../src/adapter/common.js';
import { LLMAdapter } from '../src/adapter/core.js';
import {
  ProviderAuthError,
  ProviderRateLimitError,
  ProviderServerError,
} from '../src/errors.js';
import { buildTools } from '../src/mappers/chatCompletions.js';
import { StreamEvent } from '../src/schemas.js';
import type { ChatRequest, SystemBlock } from '../src/schemas.js';

/**
 * Langfuse-shaped observation handle recording its lifecycle for assertions —
 * the TS twin of Python's `_FakeCtx`. `entered` flips when the mocked factory
 * hands it to the adapter; `updates` captures the clean-completion update
 * (absent on the teardown path); `ended` counts `end()` calls and
 * `endErrors` collects the errors passed to them (Python: the exc_info the
 * context's `__exit__` received — empty means every close was clean).
 */
class FakeObs {
  entered = false;
  updates: AnyRecord[] = [];
  ended = 0;
  endErrors: unknown[] = [];
  update(data: AnyRecord): void {
    this.updates.push(data);
  }
  end(error?: unknown): void {
    this.ended += 1;
    if (error !== undefined && error !== null) {
      this.endErrors.push(error);
    }
  }
}

function installGeneration(ctx: FakeObs): void {
  mockLangfuse.generation = () => {
    ctx.entered = true;
    return ctx;
  };
}

function installSpan(ctx: FakeObs): void {
  mockLangfuse.span = () => {
    ctx.entered = true;
    return ctx;
  };
}

/**
 * Adapter whose `responses.create` streams `events` (or resolves to the
 * single `events` object when it isn't an array — the non-streaming shape),
 * recording the create kwargs into `captured`.
 */
function responsesAdapter(events: AnyRecord[] | AnyRecord, captured?: AnyRecord): LLMAdapter {
  const client = {
    responses: {
      create: async (kwargs: AnyRecord) => {
        if (captured !== undefined) {
          Object.assign(captured, kwargs);
        }
        if (!Array.isArray(events)) {
          return events;
        }
        return (async function* () {
          for (const e of events) {
            yield e;
          }
        })();
      },
    },
  };
  return new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: client as unknown as OpenAI,
  });
}

function chatAdapter(chunks: AnyRecord[]): LLMAdapter {
  const client = {
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
  };
  return new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: client as unknown as OpenAI,
  });
}

function delta(
  d: { content?: string; reasoning_content?: string; tool_calls?: AnyRecord[] } = {},
): AnyRecord {
  return {
    content: d.content ?? null,
    reasoning_content: d.reasoning_content ?? null,
    tool_calls: d.tool_calls ?? null,
  };
}

function choice(d: AnyRecord, finishReason: string | null = null): AnyRecord {
  return { delta: d, finish_reason: finishReason };
}

function chunk(choices: AnyRecord[], usage: AnyRecord | null = null): AnyRecord {
  return { choices, usage };
}

function toolFrag(
  index: number,
  f: { id?: string; name?: string; arguments?: string } = {},
): AnyRecord {
  return {
    index,
    id: f.id ?? null,
    function: { name: f.name ?? null, arguments: f.arguments ?? null },
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
  mockLangfuse.generation = () => null;
  mockLangfuse.span = () => null;
});

// ---------------------------------------------------------------- generate()

it('generate forwards max output tokens', async () => {
  const captured: AnyRecord = {};
  const adapter = responsesAdapter({ output: [], usage: null }, captured);
  const req: ChatRequest = {
    model: 'm',
    messages: [{ role: 'user', content: 'hi' }],
    maxOutputTokens: 4096,
  };
  await adapter.generate(req, 's');
  expect(captured['max_output_tokens']).toBe(4096);
});

it('generate omits cap when unset', async () => {
  const captured: AnyRecord = {};
  const adapter = responsesAdapter({ output: [], usage: null }, captured);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'hi' }] };
  await adapter.generate(req, 's');
  // Key ABSENT from the wire kwargs — not present-with-undefined.
  expect('max_output_tokens' in captured).toBe(false);
});

// ------------------------------------------------- Langfuse context release

it('responses stream releases langfuse ctx on abandon', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const events = [
    { type: 'response.output_text.delta', delta: 'one' },
    { type: 'response.output_text.delta', delta: 'two' },
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'hi' }] };

  const agen = adapter.stream(req, 'plain system'); // unmarked → Responses path
  const iter = agen[Symbol.asyncIterator]();
  const first = await iter.next();
  expect((first.value as StreamEvent).event).toBe('text_delta');
  await iter.return(undefined); // consumer walks away mid-stream

  expect(ctx.entered).toBe(true);
  // Teardown path (Python: __exit__ got GeneratorExit): the observation
  // ended exactly once, without the clean-completion update ever firing —
  // and without an error recorded (abandonment is not a failure).
  expect(ctx.ended).toBe(1);
  expect(ctx.updates).toEqual([]);
  expect(ctx.endErrors).toEqual([]);
});

it('chat stream releases langfuse ctx on abandon', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const chunks = [
    chunk([choice(delta({ content: 'one' }))]),
    chunk([choice(delta({ content: 'two' }))]),
  ];
  const adapter = chatAdapter(chunks);
  const req: ChatRequest = { model: 'claude-x', messages: [{ role: 'user', content: 'hi' }] };

  // Cache-flagged blocks → routed to the chat.completions path.
  const system: SystemBlock[] = [
    { text: 'S', cache: true },
    { text: 't' },
  ];
  const agen = adapter.stream(req, system);
  const iter = agen[Symbol.asyncIterator]();
  const first = await iter.next();
  expect((first.value as StreamEvent).event).toBe('text_delta');
  await iter.return(undefined);

  expect(ctx.entered).toBe(true);
  expect(ctx.ended).toBe(1);
  expect(ctx.updates).toEqual([]);
  expect(ctx.endErrors).toEqual([]);
});

it('responses stream closes ctx cleanly on completion', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const events = [{ type: 'response.output_text.delta', delta: 'hi' }];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await collect(adapter.stream(req, 'plain'));

  // Clean exit (Python: the (None, None, None) triple): ended exactly once,
  // after the clean-completion update ran.
  expect(ctx.ended).toBe(1);
  expect(ctx.updates).toHaveLength(1);
  expect(ctx.endErrors).toEqual([]);
});

// ------------------------------------------------- errored observation close
// Python closes every observation with the propagating exception
// (`__exit__(*sys.exc_info())` / `__exit__(type(e), e, tb)`), so failed calls
// surface as ERRORED observations in Langfuse. The port must hand the error
// to `end()` — a bare close would make provider outages look like clean,
// empty responses in the traces.

it('responses stream closes ctx errored on provider failure', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {
      responses: { create: async () => Promise.reject(new APIError(429, undefined, 'rate limited', undefined)) },
    } as unknown as OpenAI,
  });
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await expect(collect(adapter.stream(req, 'plain'))).rejects.toBeInstanceOf(
    ProviderRateLimitError,
  );
  expect(ctx.ended).toBe(1);
  expect(ctx.updates).toEqual([]); // no clean-completion update on failure
  expect(ctx.endErrors).toHaveLength(1);
  expect(ctx.endErrors[0]).toBeInstanceOf(ProviderRateLimitError);
});

it('chat stream closes ctx errored on provider failure', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {
      chat: {
        completions: {
          create: async () => Promise.reject(new APIError(500, undefined, 'boom', undefined)),
        },
      },
    } as unknown as OpenAI,
  });
  const req: ChatRequest = { model: 'claude-x', messages: [{ role: 'user', content: 'q' }] };
  const system: SystemBlock[] = [{ text: 'S', cache: true }];

  await expect(collect(adapter.stream(req, system))).rejects.toBeInstanceOf(ProviderServerError);
  expect(ctx.ended).toBe(1);
  expect(ctx.endErrors).toHaveLength(1);
  expect(ctx.endErrors[0]).toBeInstanceOf(ProviderServerError);
});

it('generate and image close ctx errored on failure', async () => {
  const genCtx = new FakeObs();
  installGeneration(genCtx);
  const failing = async (): Promise<never> =>
    Promise.reject(new APIError(429, undefined, 'rate limited', undefined));
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {
      responses: { create: failing },
      images: { generate: failing },
    } as unknown as OpenAI,
  });
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await expect(adapter.generate(req, 'sys')).rejects.toBeInstanceOf(ProviderRateLimitError);
  expect(genCtx.ended).toBe(1);
  expect(genCtx.endErrors).toHaveLength(1);
  expect(genCtx.endErrors[0]).toBeInstanceOf(ProviderRateLimitError);

  const imgCtx = new FakeObs();
  installGeneration(imgCtx);
  await expect(adapter.generateImage({ model: 'm', prompt: 'p' })).rejects.toBeInstanceOf(
    ProviderRateLimitError,
  );
  expect(imgCtx.ended).toBe(1);
  expect(imgCtx.endErrors).toHaveLength(1);
});

it('generate chat completion closes ctx errored on failure', async () => {
  const ctx = new FakeObs();
  installGeneration(ctx);
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {
      chat: {
        completions: {
          create: async () => Promise.reject(new APIError(401, undefined, 'bad key', undefined)),
        },
      },
    } as unknown as OpenAI,
  });

  await expect(
    adapter.generateChatCompletion({ model: 'm', systemPrompt: 's', userContent: 'u' }),
  ).rejects.toBeInstanceOf(ProviderAuthError);
  expect(ctx.ended).toBe(1);
  expect(ctx.endErrors).toHaveLength(1);
  expect(ctx.endErrors[0]).toBeInstanceOf(ProviderAuthError);
});

it('complete closes span errored on inner-stream failure', async () => {
  const ctx = new FakeObs();
  installSpan(ctx);
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {
      responses: { create: async () => Promise.reject(new APIError(500, undefined, 'boom', undefined)) },
    } as unknown as OpenAI,
  });
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await expect(collect(adapter.complete(req, 'sys'))).rejects.toBeInstanceOf(ProviderServerError);
  expect(ctx.ended).toBe(1);
  expect(ctx.endErrors).toHaveLength(1);
  expect(ctx.endErrors[0]).toBeInstanceOf(ProviderServerError);
});

// ---------------------------------------------------- malformed text_delta

it('complete fails loudly on a text_delta without text', async () => {
  // Python crashes on the malformed event (KeyError / TypeError in join);
  // silently coercing would hand the caller truncated output with no error.
  // The stream override stands in for a buggy inner mapper.
  class BadStreamAdapter extends LLMAdapter {
    override stream(): AsyncGenerator<StreamEvent> {
      return (async function* () {
        yield new StreamEvent('text_delta', { text: 'ok' });
        yield new StreamEvent('text_delta', {}); // no text payload
      })();
    }
  }
  const adapter = new BadStreamAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: {} as unknown as OpenAI,
  });
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await expect(collect(adapter.complete(req, 'sys'))).rejects.toThrow(TypeError);
});

// ------------------------------------------------------- usage-less terminal

it('responses stream survives missing usage', async () => {
  const events = [
    { type: 'response.output_text.delta', delta: 'hi' },
    { type: 'response.completed', response: { usage: null, output: [] } },
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'plain'));

  expect(evs.at(-1)!.event).toBe('done');
  expect(evs.at(-1)!.data['usage']).toEqual({
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  });
});

// --------------------------------------------- deferred tool_call_start id

it('chat tool start deferred until identity arrives', async () => {
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [
    // Args-only first fragment: no id/name yet — nothing may be emitted, or
    // consumers would correlate on an empty call_id.
    chunk([choice(delta({ tool_calls: [toolFrag(0, { arguments: '{"a":' })] }))]),
    // Identity arrives: start fires with the real id and the buffered
    // fragment flushes together with the new one.
    chunk([
      choice(delta({ tool_calls: [toolFrag(0, { id: 'call_9', name: 'Foo', arguments: '1}' })] })),
    ]),
    chunk([choice(delta(), 'tool_calls')]),
    chunk([], usage),
  ];
  const adapter = chatAdapter(chunks);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'x' }] };

  const evs = await collect(
    adapterChat.stream(adapter.client, req, systemBlocks('system text'), {
      reasoningEffort: adapter.reasoningEffort,
    }),
  );

  expect(evs.map((e) => e.event)).toEqual([
    'tool_call_start',
    'tool_call_delta',
    'tool_call_done',
    'done',
  ]);
  expect(evs[0]!.data).toEqual({ call_id: 'call_9', name: 'Foo' });
  expect(evs[1]!.data).toEqual({ call_id: 'call_9', delta: '{"a":1}' });
  expect(evs[2]!.data['arguments']).toBe('{"a":1}');
});

it('chat flush pairs start with done for unidentified call', async () => {
  // Degenerate stream where no fragment ever carries id/name: the trailing
  // flush still emits a paired start → done (with the accumulated args).
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [
    chunk([choice(delta({ tool_calls: [toolFrag(0, { arguments: '{}' })] }))]),
    chunk([], usage), // no finish_reason, identity never arrived
  ];
  const adapter = chatAdapter(chunks);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'x' }] };

  const evs = await collect(
    adapterChat.stream(adapter.client, req, systemBlocks('system text'), {
      reasoningEffort: adapter.reasoningEffort,
    }),
  );

  expect(evs.map((e) => e.event)).toEqual(['tool_call_start', 'tool_call_done', 'done']);
  expect(evs[1]!.data['arguments']).toBe('{}');
});

// ------------------------------------------------------ hosted-tool warning

it('build tools warns when dropping hosted tool', () => {
  // console.warn is the port's logger.warning; the dropped type must be
  // named in the message — silent drops are a regression.
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    const out = buildTools([{ type: 'web_search_preview' }]);
    expect(out).toEqual([]);
    expect(warnSpy).toHaveBeenCalled();
    const logged = warnSpy.mock.calls.map((args) => args.map(String).join(' ')).join('\n');
    expect(logged).toContain('web_search_preview');
  } finally {
    warnSpy.mockRestore();
  }
});
