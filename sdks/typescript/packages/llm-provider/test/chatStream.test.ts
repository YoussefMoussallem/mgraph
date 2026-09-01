/**
 * `adapter/chatCompletions.stream` — the cacheable main-loop path.
 * TS twin of Python `tests/test_chat_stream.py`.
 *
 * The agent loop streams over chat.completions (the only path where LiteLLM
 * forwards content-block `cache_control` to Bedrock). This pins the parts
 * that differ from the Responses path and are easy to get wrong:
 *
 * - tool calls arrive as `delta.tool_calls` fragments keyed by array
 *   **index** (id + name on the first fragment, JSON arguments split across
 *   the rest), with no per-call "done" event — completion is a
 *   `finish_reason`;
 * - usage (incl. cache tokens) rides a final `choices: []` chunk that only
 *   appears because we send `stream_options: {include_usage: true}`;
 * - the request must carry the system prompt as `cache_control` blocks,
 *   function tools nested under `"function"`, and `reasoning_effort` when
 *   thinking.
 */

import { expect, it } from 'vitest';
import type OpenAI from 'openai';

import { stream } from '../src/adapter/chatCompletions.js';
import { systemBlocks } from '../src/adapter/common.js';
import type { ChatRequest, StreamEvent, SystemBlock } from '../src/schemas.js';

/** Drive the chat path directly (the facade routes here on caching intent). */
function chatStream(
  client: OpenAI,
  req: ChatRequest,
  system: string | SystemBlock[],
): AsyncGenerator<StreamEvent> {
  return stream(client, req, systemBlocks(system), { reasoningEffort: 'medium' });
}

function delta(
  d: { content?: string; reasoning_content?: string; tool_calls?: unknown[] } = {},
): Record<string, unknown> {
  return {
    content: d.content ?? null,
    reasoning_content: d.reasoning_content ?? null,
    tool_calls: d.tool_calls ?? null,
  };
}

function choice(d: Record<string, unknown>, finishReason: string | null = null) {
  return { delta: d, finish_reason: finishReason };
}

function chunk(choices: unknown[], usage: unknown = null) {
  return { choices, usage };
}

function toolFrag(
  index: number,
  f: { id?: string; name?: string; arguments?: string } = {},
): Record<string, unknown> {
  return {
    index,
    id: f.id ?? null,
    function: { name: f.name ?? null, arguments: f.arguments ?? null },
  };
}

/**
 * Fake client whose `chat.completions.create` yields `chunks` and records
 * the kwargs it was called with into `captured` — the TS twin of the Python
 * `_stub_adapter` (constructor client injection replaces monkeypatching).
 */
function stubClient(chunks: unknown[], captured: Record<string, unknown>): OpenAI {
  const create = async (kwargs: Record<string, unknown>) => {
    Object.assign(captured, kwargs);
    return (async function* () {
      for (const c of chunks) {
        yield c;
      }
    })();
  };
  return { chat: { completions: { create } } } as unknown as OpenAI;
}

async function collect(gen: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of gen) {
    events.push(ev);
  }
  return events;
}

it('chat stream maps text, thinking, tools and usage', async () => {
  const usage = {
    prompt_tokens: 100,
    completion_tokens: 20,
    prompt_tokens_details: { cached_tokens: 80 },
    cache_creation_input_tokens: 15,
  };
  const chunks = [
    chunk([choice(delta({ content: 'Hello' }))]),
    chunk([choice(delta({ reasoning_content: 'hmm' }))]),
    // tool call: id + name on the first fragment, arguments split across two.
    chunk([
      choice(
        delta({
          tool_calls: [toolFrag(0, { id: 'call_1', name: 'CreateSlide', arguments: '{"ti' })],
        }),
      ),
    ]),
    chunk([choice(delta({ tool_calls: [toolFrag(0, { arguments: 'tle":"X"}' })] }))]),
    chunk([choice(delta(), 'tool_calls')]),
    // usage-only terminal chunk (choices empty) — only present because of
    // stream_options include_usage.
    chunk([], usage),
  ];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);

  const req: ChatRequest = {
    model: 'claude-test',
    messages: [{ role: 'user', content: 'hi' }],
    tools: [
      {
        type: 'function',
        name: 'CreateSlide',
        description: 'make a slide',
        parameters: { type: 'object', properties: {} },
      },
    ],
    thinking: true,
    cacheTtl: '5m',
  };
  const system: SystemBlock[] = [
    { text: 'STATIC RULES', cache: true },
    { text: 'volatile tail' },
  ];

  const events = (await collect(chatStream(client, req, system))).map(
    (ev) => [ev.event, ev.data] as const,
  );
  const kinds = events.map(([e]) => e);

  // Sequence: text, thinking, one tool_call_start, two deltas, one
  // tool_call_done, then the terminal done envelope.
  expect(kinds).toEqual([
    'text_delta',
    'thinking_delta',
    'tool_call_start',
    'tool_call_delta',
    'tool_call_delta',
    'tool_call_done',
    'done',
  ]);

  const by: Record<string, Record<string, any>[]> = {};
  for (const [e, d] of events) {
    (by[e] ??= []).push(d);
  }

  expect(by['text_delta']![0]!['text']).toBe('Hello');
  expect(by['thinking_delta']![0]!['text']).toBe('hmm');
  expect(by['tool_call_start']![0]).toEqual({ call_id: 'call_1', name: 'CreateSlide' });
  // arguments reassembled in order from the streamed fragments.
  const doneTc = by['tool_call_done']![0]!;
  expect(doneTc['call_id']).toBe('call_1');
  expect(doneTc['name']).toBe('CreateSlide');
  expect(doneTc['arguments']).toBe('{"title":"X"}');

  // usage decomposed: net input, output, cache read + write.
  expect(by['done']![0]!['usage']).toEqual({
    input_tokens: 100,
    output_tokens: 20,
    cache_read_tokens: 80,
    cache_write_tokens: 15,
  });

  // Request shape: system as cache_control blocks, include_usage on,
  // function tool nested under "function", reasoning_effort set for
  // thinking.
  const sysMsg = (captured['messages'] as any[])[0];
  expect(sysMsg.role).toBe('system');
  expect(sysMsg.content[0].text).toBe('STATIC RULES');
  expect(sysMsg.content[0].cache_control).toEqual({ type: 'ephemeral' });
  expect(captured['stream_options']).toEqual({ include_usage: true });
  expect((captured['tools'] as any[])[0].type).toBe('function');
  expect((captured['tools'] as any[])[0].function.name).toBe('CreateSlide');
  expect('reasoning_effort' in captured).toBe(true);
});

it('chat stream forwards maxOutputTokens as max_tokens', async () => {
  // The cacheable chat.completions path must honour `maxOutputTokens` —
  // the main agent loop routes here, not the Responses path.
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [chunk([choice(delta({ content: 'ok' }), 'stop')]), chunk([], usage)];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);
  const req: ChatRequest = {
    model: 'claude-test',
    messages: [{ role: 'user', content: 'hi' }],
    maxOutputTokens: 16384,
  };
  const system: SystemBlock[] = [{ text: 'RULES', cache: true }, { text: 'tail' }];

  await collect(chatStream(client, req, system));
  expect(captured['max_tokens']).toBe(16384);
});

it('chat stream closes tool call without finish_reason', async () => {
  // If the proxy omits finish_reason (some do on the usage-only chunk), the
  // trailing flush still emits tool_call_done so the loop isn't left
  // hanging.
  const usage = { prompt_tokens: 5, completion_tokens: 1 };
  const chunks = [
    chunk([
      choice(delta({ tool_calls: [toolFrag(0, { id: 'c1', name: 'ListSlides', arguments: '{}' })] })),
    ]),
    chunk([], usage), // stream ends with no finish_reason anywhere
  ];
  const client = stubClient(chunks, {});
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'x' }] };
  const system: SystemBlock[] = [{ text: 'S', cache: true }, { text: 't' }];

  const kinds = (await collect(chatStream(client, req, system))).map((ev) => ev.event);
  expect(kinds).toEqual(['tool_call_start', 'tool_call_delta', 'tool_call_done', 'done']);
});

it('chat stream fails loudly on an index-less tool-call fragment', async () => {
  // chat.completions correlates streamed tool-call fragments by `index`.
  // Python reads it off the typed SDK model, so a fragment without one
  // errors the stream (untranslated); silently keying on undefined would
  // merge distinct calls into one corrupted phantom call. The port must be
  // just as loud.
  const chunks = [
    chunk([
      choice(
        delta({
          // No `index` on the fragment (some proxies emit this).
          tool_calls: [{ id: 'call_1', function: { name: 'a', arguments: '{"x":1}' } }],
        }),
      ),
    ]),
  ];
  const client = stubClient(chunks, {});
  const req: ChatRequest = { model: 'm', messages: [{ role: 'user', content: 'x' }] };
  const system: SystemBlock[] = [{ text: 'S', cache: true }];

  await expect(collect(chatStream(client, req, system))).rejects.toThrow(TypeError);
  await expect(collect(chatStream(client, req, system))).rejects.toThrow(/index/);
});

it('chat stream caches last message when message ttl set', async () => {
  // `messageCacheTtl` attaches a 5m cache_control breakpoint to the FINAL
  // conversation message (so the system→history prefix caches), while the
  // system message keeps its own longer tier and earlier messages stay
  // uncached.
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [chunk([choice(delta({ content: 'ok' }), 'stop')]), chunk([], usage)];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);
  const req: ChatRequest = {
    model: 'claude-test',
    messages: [
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'reply' },
      { role: 'user', content: 'second' },
    ],
    cacheTtl: '1h',
    messageCacheTtl: '5m',
  };
  const system: SystemBlock[] = [{ text: 'RULES', cache: true }, { text: 'tail' }];

  await collect(chatStream(client, req, system));
  const msgs = captured['messages'] as any[];

  // System keeps its own 1h tier on the cached (pre-marker) block.
  expect(msgs[0].role).toBe('system');
  expect(msgs[0].content[0].cache_control).toEqual({ type: 'ephemeral', ttl: '1h' });
  // The LAST message carries the bare 5m breakpoint (string promoted to a
  // block).
  const last = msgs[msgs.length - 1];
  expect(last.role).toBe('user');
  expect(last.content[last.content.length - 1].text).toBe('second');
  expect(last.content[last.content.length - 1].cache_control).toEqual({ type: 'ephemeral' });
  // An earlier message stays a plain string — only the tail gets a
  // breakpoint.
  expect(msgs[1].content).toBe('first');
  expect(msgs[2].content).toBe('reply');
});

it('chat stream no message cache without ttl', async () => {
  // Without `messageCacheTtl` the conversation messages stay uncached.
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [chunk([choice(delta({ content: 'ok' }), 'stop')]), chunk([], usage)];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);
  const req: ChatRequest = { model: 'claude-test', messages: [{ role: 'user', content: 'hi' }] };
  const system: SystemBlock[] = [{ text: 'R', cache: true }, { text: 't' }];

  await collect(chatStream(client, req, system));
  // Last message is a plain string — no cache_control wrapping.
  const msgs = captured['messages'] as any[];
  expect(msgs[msgs.length - 1].content).toBe('hi');
});

it('chat stream strips cache_control for openai', async () => {
  // OpenAI caches automatically by prefix and rejects Anthropic
  // `cache_control`. For a GPT model the system prompt is sent as PLAIN
  // text (markers stripped) and the conversation carries no breakpoint —
  // even when both cache TTLs are set — so the proxy never forwards a
  // foreign field.
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [chunk([choice(delta({ content: 'ok' }), 'stop')]), chunk([], usage)];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);
  const req: ChatRequest = {
    model: 'gpt-4o',
    messages: [{ role: 'user', content: 'hi' }],
    cacheTtl: '1h',
    messageCacheTtl: '5m',
  };
  const system: SystemBlock[] = [{ text: 'RULES', cache: true }, { text: 'tail' }];

  await collect(chatStream(client, req, system));
  const msgs = captured['messages'] as any[];
  // System is a plain string with the marker stripped — no cache_control
  // blocks.
  expect(msgs[0].role).toBe('system');
  expect(msgs[0].content).toBe('RULEStail');
  // Last message stays a plain string — no breakpoint despite
  // messageCacheTtl.
  expect(msgs[msgs.length - 1].content).toBe('hi');
});

it('chat stream keeps cache_control for gemini', async () => {
  // LiteLLM forwards cache_control to Gemini (it maps onto Gemini context
  // caching), so a Gemini model keeps the cache_control blocks — dropping
  // them would DISABLE its caching. Only OpenAI is sent plain text.
  const usage = { prompt_tokens: 1, completion_tokens: 1 };
  const chunks = [chunk([choice(delta({ content: 'ok' }), 'stop')]), chunk([], usage)];
  const captured: Record<string, unknown> = {};
  const client = stubClient(chunks, captured);
  const req: ChatRequest = {
    model: 'gemini-2.5-pro',
    messages: [{ role: 'user', content: 'hi' }],
    cacheTtl: '1h',
    messageCacheTtl: '5m',
  };
  const system: SystemBlock[] = [{ text: 'RULES', cache: true }, { text: 'tail' }];

  await collect(chatStream(client, req, system));
  const msgs = captured['messages'] as any[];
  // System split into cache_control blocks (not plain text).
  expect(msgs[0].content[0].cache_control).toEqual({ type: 'ephemeral', ttl: '1h' });
  // Last message carries the 5m breakpoint.
  const last = msgs[msgs.length - 1];
  expect(last.content[last.content.length - 1].cache_control).toEqual({ type: 'ephemeral' });
});
