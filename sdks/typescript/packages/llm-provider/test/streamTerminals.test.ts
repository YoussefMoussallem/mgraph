/**
 * Stream terminals, cancellation, and per-request routing/effort — the
 * 2026-07 contract additions. Python twin: `tests/test_stream_terminals.py`
 * (pending port — see PARITY.md "Pending parity").
 *
 * Pins:
 *
 * - `done.stop_reason` on both wire paths: chat `finish_reason` mapping,
 *   Responses `response.completed` / `response.incomplete` /
 *   `response.failed` / standalone `error` items, and `null` when the
 *   stream ends with no terminal signal (completion unconfirmed);
 * - `response.incomplete` is a TERMINAL that surfaces the usage it burned —
 *   truncation must never look like a clean zero-usage success;
 * - `ChatRequest.signal` / `GenerateChatCompletionOptions.signal` /
 *   `GenerateImageOptions.signal` forwarding as SDK request options, and
 *   `APIUserAbortError` passing through `toProviderError` UNTRANSLATED;
 * - statusless `APIError` (mid-stream SSE error payload) translating to
 *   `ProviderServerError` instead of leaking the SDK type;
 * - `ChatRequest.transport` overriding the caching-intent routing rule;
 * - `ChatRequest.reasoningEffort` overriding the adapter default;
 * - `complete()` forwarding the inner `stop_reason` on its outer `done`.
 */

import { expect, it } from 'vitest';
import type OpenAI from 'openai';
import { APIError, APIUserAbortError } from 'openai';

import { LLMAdapter } from '../src/adapter/core.js';
import { stream as chatStream } from '../src/adapter/chatCompletions.js';
import { mapFinishReason, systemBlocks, toProviderError } from '../src/adapter/common.js';
import { ProviderServerError } from '../src/errors.js';
import type { ChatRequest, StreamEvent } from '../src/schemas.js';

type AnyRecord = Record<string, any>;

async function collect(gen: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of gen) {
    events.push(ev);
  }
  return events;
}

function doneEvent(events: StreamEvent[]): StreamEvent {
  const done = events.find((e) => e.event === 'done');
  expect(done).toBeDefined();
  return done!;
}

// ---------------------------------------------------------------- chat path

function chatChunk(choices: unknown[], usage: unknown = null): AnyRecord {
  return { choices, usage };
}

function chatChoice(delta: AnyRecord, finishReason: string | null = null): AnyRecord {
  return { delta, finish_reason: finishReason };
}

/**
 * Fake chat client recording kwargs AND the per-call request options —
 * the second `create` argument, where `signal` rides.
 */
function chatClient(
  chunks: unknown[],
  captured: AnyRecord = {},
  capturedOptions: AnyRecord = {},
): OpenAI {
  const create = async (kwargs: AnyRecord, requestOptions?: AnyRecord) => {
    Object.assign(captured, kwargs);
    Object.assign(capturedOptions, requestOptions ?? {});
    return (async function* () {
      for (const c of chunks) {
        yield c;
      }
    })();
  };
  return { chat: { completions: { create } } } as unknown as OpenAI;
}

function runChat(
  client: OpenAI,
  request: ChatRequest,
  system: string = 'sys',
): AsyncGenerator<StreamEvent> {
  return chatStream(client, request, systemBlocks(system), { reasoningEffort: 'medium' });
}

it('chat finish_reason stop maps to end_turn on done', async () => {
  const client = chatClient([
    chatChunk([chatChoice({ content: 'hi' }, 'stop')]),
    chatChunk([], { prompt_tokens: 3, completion_tokens: 1 }),
  ]);
  const events = await collect(runChat(client, { model: 'claude-x', messages: [] }));
  expect(doneEvent(events).data['stop_reason']).toBe('end_turn');
});

it('chat finish_reason length maps to max_tokens', async () => {
  const client = chatClient([chatChunk([chatChoice({ content: 'trunc' }, 'length')])]);
  const events = await collect(runChat(client, { model: 'claude-x', messages: [] }));
  expect(doneEvent(events).data['stop_reason']).toBe('max_tokens');
});

it('chat finish_reason tool_calls maps to tool_use and still flushes dones', async () => {
  const frag = {
    index: 0,
    id: 'call_1',
    function: { name: 'lookup', arguments: '{"q":1}' },
  };
  const client = chatClient([
    chatChunk([chatChoice({ tool_calls: [frag] }, null)]),
    chatChunk([chatChoice({}, 'tool_calls')]),
  ]);
  const events = await collect(runChat(client, { model: 'claude-x', messages: [] }));
  expect(events.map((e) => e.event)).toEqual([
    'tool_call_start',
    'tool_call_delta',
    'tool_call_done',
    'done',
  ]);
  expect(doneEvent(events).data['stop_reason']).toBe('tool_use');
});

it('chat stream with no finish_reason reports stop_reason null', async () => {
  const client = chatClient([chatChunk([chatChoice({ content: 'partial' }, null)])]);
  const events = await collect(runChat(client, { model: 'claude-x', messages: [] }));
  expect(doneEvent(events).data['stop_reason']).toBeNull();
});

it('unknown finish reasons pass through verbatim', () => {
  expect(mapFinishReason('content_filter')).toBe('content_filter');
  expect(mapFinishReason('function_call')).toBe('tool_use');
  expect(mapFinishReason(null)).toBeNull();
  expect(mapFinishReason('')).toBeNull();
});

it('chat stream forwards the abort signal as a request option', async () => {
  const capturedOptions: AnyRecord = {};
  const controller = new AbortController();
  const client = chatClient([chatChunk([chatChoice({ content: 'x' }, 'stop')])], {}, capturedOptions);
  await collect(
    runChat(client, { model: 'claude-x', messages: [], signal: controller.signal }),
  );
  expect(capturedOptions['signal']).toBe(controller.signal);
});

it('chat per-request reasoningEffort overrides the adapter default', async () => {
  const captured: AnyRecord = {};
  const client = chatClient([], captured);
  await collect(
    runChat(client, { model: 'claude-x', messages: [], thinking: true, reasoningEffort: 'high' }),
  );
  expect(captured['reasoning_effort']).toBe('high');
});

// ----------------------------------------------------------- responses path

function ev(type: string, fields: AnyRecord = {}): AnyRecord {
  return { type, ...fields };
}

function responsesClient(
  events: AnyRecord[],
  captured: AnyRecord = {},
  capturedOptions: AnyRecord = {},
): AnyRecord {
  return {
    responses: {
      create: async (kwargs: AnyRecord, requestOptions?: AnyRecord) => {
        Object.assign(captured, kwargs);
        Object.assign(capturedOptions, requestOptions ?? {});
        return (async function* () {
          for (const item of events) {
            yield item;
          }
        })();
      },
    },
  };
}

function adapterFor(client: AnyRecord): LLMAdapter {
  return new LLMAdapter({ apiKey: 't', baseUrl: 'http://localhost/v1', client: client as any });
}

const USAGE = { input_tokens: 10, output_tokens: 4 };

it('response.completed without tool calls reports end_turn', async () => {
  const client = responsesClient([
    ev('response.output_text.delta', { delta: 'hello' }),
    ev('response.completed', { response: { usage: USAGE, output: [] } }),
  ]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  const done = doneEvent(events);
  expect(done.data['stop_reason']).toBe('end_turn');
  expect(done.data['usage']['input_tokens']).toBe(10);
});

it('response.completed with function calls reports tool_use', async () => {
  const client = responsesClient([
    ev('response.output_item.added', {
      item: { type: 'function_call', id: 'it_1', call_id: 'call_1', name: 'lookup' },
    }),
    ev('response.function_call_arguments.done', { item_id: 'it_1', arguments: '{}' }),
    ev('response.completed', { response: { usage: USAGE, output: [] } }),
  ]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  expect(doneEvent(events).data['stop_reason']).toBe('tool_use');
});

it('response.incomplete surfaces max_tokens AND the usage it burned', async () => {
  const client = responsesClient([
    ev('response.output_text.delta', { delta: 'truncated outp' }),
    ev('response.incomplete', {
      response: {
        usage: { input_tokens: 900, output_tokens: 4096 },
        incomplete_details: { reason: 'max_output_tokens' },
        output: [],
      },
    }),
  ]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  const done = doneEvent(events);
  expect(done.data['stop_reason']).toBe('max_tokens');
  // The truncated call must never look free: real token counts survive.
  expect(done.data['usage']['input_tokens']).toBe(900);
  expect(done.data['usage']['output_tokens']).toBe(4096);
});

it('response.incomplete with an unknown/missing reason degrades truthfully', async () => {
  const known = responsesClient([
    ev('response.incomplete', {
      response: { usage: USAGE, incomplete_details: { reason: 'content_filter' }, output: [] },
    }),
  ]);
  const knownEvents = await collect(adapterFor(known).stream({ model: 'gpt-x', messages: [] }, ''));
  expect(doneEvent(knownEvents).data['stop_reason']).toBe('content_filter');

  const bare = responsesClient([ev('response.incomplete', { response: { usage: USAGE, output: [] } })]);
  const bareEvents = await collect(adapterFor(bare).stream({ model: 'gpt-x', messages: [] }, ''));
  expect(doneEvent(bareEvents).data['stop_reason']).toBe('incomplete');
});

it('response.failed keeps the soft error event and marks done as error with usage', async () => {
  const client = responsesClient([
    ev('response.failed', {
      response: { error: { message: 'upstream exploded' }, usage: USAGE, output: [] },
    }),
  ]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  const error = events.find((e) => e.event === 'error');
  expect(error?.data['message']).toBe('upstream exploded');
  const done = doneEvent(events);
  expect(done.data['stop_reason']).toBe('error');
  expect(done.data['usage']['input_tokens']).toBe(10);
});

it('standalone error items surface as error events instead of being dropped', async () => {
  const client = responsesClient([
    ev('error', { message: 'rate limited upstream' }),
    ev('response.completed', { response: { usage: USAGE, output: [] } }),
  ]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  const error = events.find((e) => e.event === 'error');
  expect(error?.data['message']).toBe('rate limited upstream');
});

it('a stream ending with no terminal event reports stop_reason null', async () => {
  const client = responsesClient([ev('response.output_text.delta', { delta: 'cut off' })]);
  const events = await collect(adapterFor(client).stream({ model: 'gpt-x', messages: [] }, ''));
  const done = doneEvent(events);
  expect(done.data['stop_reason']).toBeNull();
  expect(done.data['usage']['input_tokens']).toBe(0);
});

it('responses stream forwards the abort signal and per-request effort', async () => {
  const captured: AnyRecord = {};
  const capturedOptions: AnyRecord = {};
  const controller = new AbortController();
  const client = responsesClient([], captured, capturedOptions);
  await collect(
    adapterFor(client).stream(
      {
        model: 'gpt-x',
        messages: [],
        thinking: true,
        reasoningEffort: 'low',
        signal: controller.signal,
      },
      '',
    ),
  );
  expect(capturedOptions['signal']).toBe(controller.signal);
  expect((captured['reasoning'] as AnyRecord)['effort']).toBe('low');
});

// ------------------------------------------------------- transport override

it('transport "chat" forces the chat path for a cacheless request', async () => {
  const captured: AnyRecord = {};
  const client = chatClient([chatChunk([chatChoice({ content: 'x' }, 'stop')])], captured);
  const adapter = new LLMAdapter({
    apiKey: 't',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  // No cache flags anywhere — the intent rule would pick responses; the
  // explicit override must win (a responses call would throw here: the fake
  // client has no `responses` endpoint at all).
  const events = await collect(
    adapter.stream({ model: 'claude-x', messages: [], transport: 'chat' }, 'sys'),
  );
  expect(doneEvent(events).data['stop_reason']).toBe('end_turn');
  expect(captured['stream']).toBe(true);
});

it('transport "responses" forces the responses path despite caching intent', async () => {
  const client = responsesClient([ev('response.completed', { response: { usage: USAGE, output: [] } })]);
  const adapter = adapterFor(client);
  // Cache-flagged system block — the intent rule would pick chat; the fake
  // client has no chat endpoint, so passing proves the override won.
  const events = await collect(
    adapter.stream(
      { model: 'claude-x', messages: [], transport: 'responses' },
      [{ text: 'stable', cache: true }],
    ),
  );
  expect(doneEvent(events).data['stop_reason']).toBe('end_turn');
});

// ------------------------------------------------- complete() + error paths

it('complete() forwards the inner stop_reason on its outer done', async () => {
  const client = responsesClient([
    ev('response.output_text.delta', { delta: 'trunc' }),
    ev('response.incomplete', {
      response: { usage: USAGE, incomplete_details: { reason: 'max_output_tokens' }, output: [] },
    }),
  ]);
  const events = await collect(adapterFor(client).complete({ model: 'gpt-x', messages: [] }, ''));
  expect(events.map((e) => e.event)).toEqual(['text', 'done']);
  expect(doneEvent(events).data['stop_reason']).toBe('max_tokens');
});

it('APIUserAbortError passes through toProviderError untranslated', () => {
  expect(toProviderError(new APIUserAbortError())).toBeNull();
});

it('statusless APIError translates to ProviderServerError, not a leak', () => {
  const err = new APIError(undefined, undefined, 'mid-stream SSE error payload', undefined);
  const translated = toProviderError(err);
  expect(translated).toBeInstanceOf(ProviderServerError);
  expect(translated?.message).toBe('mid-stream SSE error payload');
  expect(translated?.cause).toBe(err);
});

it('generateChatCompletion forwards the abort signal', async () => {
  const capturedOptions: AnyRecord = {};
  const controller = new AbortController();
  const client = {
    chat: {
      completions: {
        create: async (_kwargs: AnyRecord, requestOptions?: AnyRecord) => {
          Object.assign(capturedOptions, requestOptions ?? {});
          return { choices: [{ message: { content: 'title' } }], usage: null };
        },
      },
    },
  };
  const adapter = new LLMAdapter({
    apiKey: 't',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  const text = await adapter.generateChatCompletion({
    model: 'gpt-x',
    systemPrompt: 'sys',
    userContent: 'name this',
    signal: controller.signal,
  });
  expect(text).toBe('title');
  expect(capturedOptions['signal']).toBe(controller.signal);
});

it('generateChatCompletion forwards maxOutputTokens and omits temperature on null', async () => {
  // Replaced (not merged) per call — the second call's assertions must see
  // ONLY its own kwargs, not the first call's leftovers.
  let captured: AnyRecord = {};
  const client = {
    chat: {
      completions: {
        create: async (kwargs: AnyRecord) => {
          captured = { ...kwargs };
          return { choices: [{ message: { content: 'ok' } }], usage: null };
        },
      },
    },
  };
  const adapter = new LLMAdapter({
    apiKey: 't',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  await adapter.generateChatCompletion({
    model: 'gpt-x',
    systemPrompt: 'sys',
    userContent: 'u',
    temperature: null,
    maxOutputTokens: 1500,
  });
  expect(captured['max_tokens']).toBe(1500);
  // null = omit entirely — the provider's own default applies, and the
  // temperature drop-and-retry never has to fire for it.
  expect('temperature' in captured).toBe(false);

  // Unset keeps the historical default.
  await adapter.generateChatCompletion({ model: 'gpt-x', systemPrompt: 'sys', userContent: 'u' });
  expect(captured['temperature']).toBe(0.3);
  expect('max_tokens' in captured).toBe(false);
});

it('generateImage forwards background/outputFormat and surfaces URL-dialect items', async () => {
  let captured: AnyRecord = {};
  const client = {
    images: {
      generate: async (kwargs: AnyRecord) => {
        captured = { ...kwargs };
        return {
          data: [
            { b64_json: Buffer.from('inline').toString('base64') },
            // URL dialect (e.g. CogView behind a proxy): no b64_json at all.
            { url: 'https://img.example/render-2.png' },
          ],
          usage: null,
        };
      },
    },
  };
  const adapter = new LLMAdapter({
    apiKey: 't',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  const { images, urls } = await adapter.generateImage({
    model: 'gpt-image-1',
    prompt: 'a transparent icon',
    background: 'transparent',
    outputFormat: 'png',
  });
  expect(captured['background']).toBe('transparent');
  expect(captured['output_format']).toBe('png');
  expect(captured['n']).toBe(1);
  expect(images).toHaveLength(1);
  expect(urls).toEqual(['https://img.example/render-2.png']);

  // n: null = omit entirely (backends that reject the param).
  await adapter.generateImage({ model: 'cogview-4', prompt: 'p', n: null });
  expect('n' in captured).toBe(false);
});

it('generateImage forwards the abort signal', async () => {
  const capturedOptions: AnyRecord = {};
  const controller = new AbortController();
  const client = {
    images: {
      generate: async (_kwargs: AnyRecord, requestOptions?: AnyRecord) => {
        Object.assign(capturedOptions, requestOptions ?? {});
        return { data: [{ b64_json: Buffer.from('png').toString('base64') }], usage: null };
      },
    },
  };
  const adapter = new LLMAdapter({
    apiKey: 't',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  const result = await adapter.generateImage({
    model: 'gpt-image-1',
    prompt: 'a chart',
    signal: controller.signal,
  });
  expect(result.images).toHaveLength(1);
  expect(capturedOptions['signal']).toBe(controller.signal);
});
