/**
 * `LLMAdapter.stream` — the Responses-API path, routing, and `complete`.
 * TS twin of Python `tests/test_responses_stream.py`.
 *
 * Pins the normalised event mapping for every Responses event we handle, the
 * intent-based routing between the two wire APIs, and `complete()`'s
 * buffer-text-forward-status contract. Fake clients are plain structural
 * objects (the Python originals use SimpleNamespace) injected through the
 * adapter's `client` constructor option.
 */

import { expect, it } from 'vitest';

import { LLMAdapter } from '../src/adapter/core.js';
import type { ChatRequest, StreamEvent, SystemBlock } from '../src/schemas.js';

type AnyRecord = Record<string, any>;

function ev(type: string, fields: AnyRecord = {}): AnyRecord {
  return { type, ...fields };
}

function completed(
  opts: {
    inputTokens?: number;
    outputTokens?: number;
    cached?: number | null;
    cacheWrite?: number | null;
    output?: unknown[];
  } = {},
): AnyRecord {
  const { inputTokens = 0, outputTokens = 0, cached = null, cacheWrite = null, output = [] } = opts;
  const usage = {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    input_tokens_details: cached != null ? { cached_tokens: cached } : null,
    cache_creation_input_tokens: cacheWrite,
  };
  return ev('response.completed', { response: { usage, output: [...output] } });
}

async function* replay(items: AnyRecord[]): AsyncGenerator<AnyRecord> {
  for (const item of items) {
    yield item;
  }
}

function responsesAdapter(events: AnyRecord[], captured?: AnyRecord): LLMAdapter {
  const client = {
    responses: {
      create: async (kwargs: AnyRecord) => {
        if (captured !== undefined) {
          Object.assign(captured, kwargs);
        }
        return replay(events);
      },
    },
  };
  return new LLMAdapter({ apiKey: 'test', baseUrl: 'http://localhost/v1', client: client as any });
}

/** Adapter exposing BOTH wire APIs; records which one served the request. */
function dualAdapter(
  respEvents: AnyRecord[],
  chatChunks: AnyRecord[],
): { adapter: LLMAdapter; served: { responses: number; chat: number }; captured: AnyRecord } {
  const served = { responses: 0, chat: 0 };
  const captured: AnyRecord = {};

  const client = {
    responses: {
      create: async (kwargs: AnyRecord) => {
        served.responses += 1;
        Object.assign(captured, kwargs);
        return replay(respEvents);
      },
    },
    chat: {
      completions: {
        create: async (kwargs: AnyRecord) => {
          served.chat += 1;
          Object.assign(captured, kwargs);
          return replay(chatChunks);
        },
      },
    },
  };
  const adapter = new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: client as any,
  });
  return { adapter, served, captured };
}

function chatOkChunks(): AnyRecord[] {
  return [
    {
      choices: [
        {
          delta: { content: 'ok', reasoning_content: null, tool_calls: null },
          finish_reason: 'stop',
        },
      ],
      usage: null,
    },
    { choices: [], usage: { prompt_tokens: 1, completion_tokens: 1 } },
  ];
}

async function collect(stream: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const event of stream) {
    events.push(event);
  }
  return events;
}

// ------------------------------------------------------------ event mapping

it('responses stream maps text, thinking, tools, and usage', async () => {
  const events = [
    ev('response.output_text.delta', { delta: 'Hi ' }),
    ev('response.reasoning_text.delta', { delta: 'think' }),
    ev('response.reasoning_summary_text.delta', { delta: 'sum' }),
    ev('response.output_item.added', {
      item: { type: 'function_call', id: 'item1', call_id: 'call_1', name: 'MakeSlide' },
    }),
    ev('response.function_call_arguments.delta', { item_id: 'item1', delta: '{"a"' }),
    ev('response.function_call_arguments.delta', { item_id: 'item1', delta: ':1}' }),
    ev('response.function_call_arguments.done', { item_id: 'item1', arguments: '{"a":1}' }),
    ev('response.some.future.event'), // unknown — must be ignored
    completed({ inputTokens: 10, outputTokens: 5, cached: 4, cacheWrite: 2 }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'sys'));
  const kinds = evs.map((e) => e.event);

  expect(kinds).toEqual([
    'text_delta',
    'thinking_delta',
    'thinking_delta',
    'tool_call_start',
    'tool_call_delta',
    'tool_call_delta',
    'tool_call_done',
    'done',
  ]);
  const start = evs[3]!;
  expect(start.data).toEqual({ call_id: 'call_1', name: 'MakeSlide' });
  const doneTc = evs[6]!;
  expect(doneTc.data).toEqual({ call_id: 'call_1', name: 'MakeSlide', arguments: '{"a":1}' });
  expect(evs.at(-1)!.data['usage']).toEqual({
    input_tokens: 10,
    output_tokens: 5,
    cache_read_tokens: 4,
    cache_write_tokens: 2,
  });
});

it('responses stream web search flow and sources', async () => {
  const ann = { type: 'url_citation', url: 'https://a.example', title: 'A' };
  const dup = { type: 'url_citation', url: 'https://a.example', title: 'A again' };
  const msgItem = { type: 'message', content: [{ annotations: [ann, dup] }] };
  const events = [
    ev('response.web_search_call.in_progress'),
    ev('response.web_search_call.searching'),
    ev('response.web_search_call.completed'),
    completed({ inputTokens: 1, outputTokens: 1, output: [msgItem] }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'sys'));
  const kinds = evs.map((e) => e.event);

  expect(kinds).toEqual([
    'web_search_start',
    'web_search_searching',
    'web_search_done',
    'web_search_sources',
    'done',
  ]);
  // Citations deduped by URL, first-seen title wins.
  expect(evs[3]!.data).toEqual({ sources: [{ url: 'https://a.example', title: 'A' }] });
});

it('responses stream degrades when terminal event has no response body', async () => {
  // A proxy may forward `response.completed` with the response stripped
  // (`response: null`). Python's getattr(None, "usage", None) degrades to
  // zero counts and still finishes with `done`; the port must not throw a
  // raw TypeError and lose the terminal envelope.
  const events = [
    ev('response.output_text.delta', { delta: 'hi' }),
    ev('response.completed', { response: null }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'sys'));

  expect(evs.map((e) => e.event)).toEqual(['text_delta', 'done']);
  expect(evs.at(-1)!.data['usage']).toEqual({
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  });
});

it('responses stream soft failure yields error, not raise', async () => {
  const events = [
    ev('response.output_text.delta', { delta: 'partial' }),
    ev('response.failed', { response: { error: { message: 'boom' } } }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'sys'));

  // Partial output preserved; error surfaced as an event; stream still
  // terminates with done (zero usage — no completed event arrived).
  expect(evs.map((e) => e.event)).toEqual(['text_delta', 'error', 'done']);
  expect(evs[1]!.data).toEqual({ message: 'boom' });
});

it('responses stream soft failure forwards a present-null message as null', async () => {
  // Python: getattr(error_obj, "message", str(error_obj)) — the fallback
  // applies only when the attribute is ABSENT. `{message: None}` reaches
  // consumers as a null message (falsy — UIs show their own copy), never a
  // useless "[object Object]" rendering.
  const events = [
    ev('response.failed', {
      response: { error: { message: null, code: 'content_policy' } },
    }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.stream(req, 'sys'));

  expect(evs.map((e) => e.event)).toEqual(['error', 'done']);
  expect(evs[0]!.data).toEqual({ message: null });
});

it('responses stream request shape', async () => {
  const captured: AnyRecord = {};
  const adapter = responsesAdapter([completed()], captured);
  const req: ChatRequest = {
    model: 'm',
    messages: [{ role: 'user', content: 'q' }],
    tools: [{ type: 'function', name: 'T', description: '', parameters: {} }],
    thinking: true,
    maxOutputTokens: 777,
  };

  await collect(adapter.stream(req, 'sys'));

  expect(captured['model']).toBe('m');
  expect(captured['instructions']).toBe('sys');
  expect(captured['stream']).toBe(true);
  expect(captured['max_output_tokens']).toBe(777);
  expect(captured['reasoning']).toEqual({ effort: 'medium', summary: 'auto' });
  expect(captured['tools'][0].name).toBe('T');
});

it('responses stream omits optional kwargs', async () => {
  const captured: AnyRecord = {};
  const adapter = responsesAdapter([completed()], captured);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  await collect(adapter.stream(req, 'sys'));

  expect('tools' in captured).toBe(false);
  expect('reasoning' in captured).toBe(false);
  expect('max_output_tokens' in captured).toBe(false);
});

// ------------------------------------------------------------------ routing

it('plain prompt routes to responses', async () => {
  const { adapter, served } = dualAdapter([completed()], chatOkChunks());
  const req: ChatRequest = { model: 'claude-x', messages: [{ role: 'user', content: 'q' }] };
  await collect(adapter.stream(req, 'plain system'));
  expect(served).toEqual({ responses: 1, chat: 0 });
});

it('cache-flagged blocks route to chat', async () => {
  const { adapter, served } = dualAdapter([completed()], chatOkChunks());
  const req: ChatRequest = { model: 'claude-x', messages: [{ role: 'user', content: 'q' }] };
  const system: SystemBlock[] = [
    { text: 'S', cache: true },
    { text: 't' },
  ];
  await collect(adapter.stream(req, system));
  expect(served).toEqual({ responses: 0, chat: 1 });
});

it('message cache ttl alone routes to chat', async () => {
  // History-only caching: no flagged blocks, but messageCacheTtl set —
  // must still reach the chat path (inexpressible under the old design).
  const { adapter, served, captured } = dualAdapter([completed()], chatOkChunks());
  const req: ChatRequest = {
    model: 'claude-x',
    messages: [{ role: 'user', content: 'q' }],
    messageCacheTtl: '5m',
  };
  await collect(adapter.stream(req, 'plain system'));
  expect(served).toEqual({ responses: 0, chat: 1 });
  // System stays plain text (no flags), but the last message carries the
  // moving history breakpoint.
  expect(captured['messages'][0].content).toBe('plain system');
  expect(captured['messages'].at(-1).content.at(-1).cache_control).toEqual({ type: 'ephemeral' });
});

// ---------------------------------------------------------------- complete()

it('complete buffers text and forwards status', async () => {
  const events = [
    ev('response.output_item.added', {
      item: { type: 'function_call', id: 'i1', call_id: 'c1', name: 'T' },
    }),
    ev('response.function_call_arguments.done', { item_id: 'i1', arguments: '{}' }),
    ev('response.output_text.delta', { delta: 'Hel' }),
    ev('response.output_text.delta', { delta: 'lo' }),
    completed({ inputTokens: 7, outputTokens: 3, cached: 5 }),
  ];
  const adapter = responsesAdapter(events);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };

  const evs = await collect(adapter.complete(req, 'sys'));
  const kinds = evs.map((e) => e.event);

  // Tool events forwarded live; text collapsed to ONE event before done.
  expect(kinds).toEqual(['tool_call_start', 'tool_call_done', 'text', 'done']);
  expect(evs[2]!.data).toEqual({ text: 'Hello' });
  // Full usage (incl. cache counters) survives the wrapper.
  expect(evs[3]!.data['usage']).toEqual({
    input_tokens: 7,
    output_tokens: 3,
    cache_read_tokens: 5,
    cache_write_tokens: 0,
  });
});

it('complete emits no text event when no text', async () => {
  const adapter = responsesAdapter([completed()]);
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'q' }] };
  const kinds = (await collect(adapter.complete(req, 'sys'))).map((e) => e.event);
  expect(kinds).toEqual(['done']);
});
