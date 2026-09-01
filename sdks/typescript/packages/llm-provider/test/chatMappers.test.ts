/**
 * `buildMessages` / `buildTools` — internal -> Chat Completions.
 * TS twin of Python `tests/test_chat_mappers.py`.
 *
 * The careful part is tool-call history: a prior assistant turn's text and
 * tool calls collapse into ONE message (content + tool_calls), and each tool
 * result becomes a `role: "tool"` message paired by `tool_call_id`. Getting
 * this wrong breaks multi-turn tool use on the chat path.
 */

import { afterEach, expect, it, vi } from 'vitest';

import { buildMessages, buildTools, type ChatContentPart } from '../src/mappers/chatCompletions.js';
import type { Message } from '../src/schemas.js';

afterEach(() => {
  vi.restoreAllMocks();
});

it('user assistant tool roundtrip', () => {
  const messages: Message[] = [
    { role: 'user', content: 'make a slide' },
    {
      role: 'assistant',
      content: 'on it',
      toolCalls: [{ id: 'c1', name: 'CreateSlide', arguments: '{"t":1}' }],
    },
    { role: 'tool', toolCallId: 'c1', content: 'ok, slide 1' },
    { role: 'assistant', content: 'done' },
  ];
  const out = buildMessages(messages);

  expect(out[0]).toEqual({ role: 'user', content: 'make a slide' });
  // assistant text + tool_calls collapse into ONE message
  expect(out[1]!.role).toBe('assistant');
  expect(out[1]!.content).toBe('on it');
  expect(out[1]!.tool_calls).toEqual([
    {
      id: 'c1',
      type: 'function',
      function: { name: 'CreateSlide', arguments: '{"t":1}' },
    },
  ]);
  // tool result pairs back by tool_call_id
  expect(out[2]).toEqual({ role: 'tool', tool_call_id: 'c1', content: 'ok, slide 1' });
  expect(out[3]).toEqual({ role: 'assistant', content: 'done' });
});

it('tool-calls-only turn sends null content', () => {
  // A tool-calls-only assistant turn must send content: null (not "") so
  // Bedrock doesn't reject an empty turn.
  const messages: Message[] = [
    { role: 'assistant', toolCalls: [{ id: 'c9', name: 'ListSlides', arguments: '{}' }] },
  ];
  const out = buildMessages(messages);
  expect(out[0]!.content).toBeNull();
  expect(out[0]!.tool_calls![0]!.id).toBe('c9');
});

it('user images become multipart', () => {
  const messages: Message[] = [
    { role: 'user', content: 'look', images: [{ mimeType: 'image/png', base64: 'QUJD' }] },
  ];
  const out = buildMessages(messages);
  const content = out[0]!.content as ChatContentPart[];
  expect(content[0]).toEqual({ type: 'text', text: 'look' });
  expect(content[1]!.type).toBe('image_url');
  expect(content[1]!.image_url!.url).toBe('data:image/png;base64,QUJD');
});

it('buildTools nests function and skips builtins', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const tools: Record<string, unknown>[] = [
    {
      type: 'function',
      name: 'CreateSlide',
      description: 'd',
      parameters: { type: 'object' },
    },
    { type: 'web_search_preview' }, // hosted built-in — unsupported on chat
    { type: 'function', name: 'NoParams' }, // missing parameters
  ];
  const out = buildTools(tools);

  expect(out).toHaveLength(2); // built-in skipped
  expect(out[0]).toEqual({
    type: 'function',
    function: {
      name: 'CreateSlide',
      description: 'd',
      parameters: { type: 'object' },
    },
  });
  // missing parameters -> empty object schema (the API requires one)
  expect(out[1]!.function.parameters).toEqual({ type: 'object', properties: {} });
  expect(out[1]!.function.name).toBe('NoParams');
  // the drop is warned, naming the dropped type — never silent
  expect(warn).toHaveBeenCalledOnce();
  expect(String(warn.mock.calls[0]![0])).toContain('web_search_preview');
});

it('empty tools is empty list', () => {
  expect(buildTools(null)).toEqual([]);
  expect(buildTools([])).toEqual([]);
});

it('buildTools drops an explicit-null type with the warning', () => {
  // Python dict.get("type", "function") defaults ONLY when the key is
  // absent: `{type: null}` is a non-function tool and must be dropped
  // loudly, not silently coerced into a function tool and sent.
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const out = buildTools([{ type: null, name: 'lookup', parameters: { type: 'object' } }]);
  expect(out).toEqual([]);
  expect(warn).toHaveBeenCalledOnce();
  expect(String(warn.mock.calls[0]![0])).toContain('null');
});

it('buildTools forwards explicit-null name and description', () => {
  // dict.get semantics again: present-but-null values go out on the wire as
  // null (Python emits `"name": null`), while ABSENT keys still default.
  const out = buildTools([{ type: 'function', name: null, description: null }]);
  expect(out[0]!.function.name).toBeNull();
  expect(out[0]!.function.description).toBeNull();

  const defaulted = buildTools([{ type: 'function' }]);
  expect(defaulted[0]!.function.name).toBe('');
  expect(defaulted[0]!.function.description).toBe('');
});
