/**
 * `buildInput` / `buildTools` — internal -> Responses API shapes.
 * TS twin of Python `tests/test_responses_mappers.py`.
 *
 * The Responses counterpart of chatMappers: assistant text and tool calls
 * become SEPARATE input items (unlike chat's single message), and tool
 * results become `function_call_output` items paired by `call_id`.
 */

import { expect, it } from 'vitest';

import { buildInput, buildTools } from '../src/mappers/responses.js';
import type { Message } from '../src/schemas.js';

it('user plain text', () => {
  const out = buildInput([{ role: 'user', content: 'hi' }]);
  expect(out).toEqual([{ role: 'user', content: 'hi' }]);
});

it('user images become multipart data urls', () => {
  const out = buildInput([
    {
      role: 'user',
      content: 'look',
      images: [{ mimeType: 'image/png', base64: 'QUJD' }],
    },
  ]);
  const content = (out[0] as { content: unknown[] }).content;
  expect(content[0]).toEqual({ type: 'input_text', text: 'look' });
  expect(content[1]).toEqual({
    type: 'input_image',
    image_url: 'data:image/png;base64,QUJD',
  });
});

it('assistant text and tool calls are separate items', () => {
  const out = buildInput([
    {
      role: 'assistant',
      content: 'on it',
      toolCalls: [{ id: 'c1', name: 'MakeSlide', arguments: '{}' }],
    },
  ]);
  // Text first (preserving the model's original ordering), then the call.
  expect(out[0]).toEqual({ role: 'assistant', content: 'on it' });
  expect(out[1]).toEqual({
    type: 'function_call',
    call_id: 'c1',
    name: 'MakeSlide',
    arguments: '{}',
  });
});

it('tool result becomes function_call_output', () => {
  const out = buildInput([{ role: 'tool', toolCallId: 'c1', content: 'done' } as Message]);
  expect(out).toEqual([{ type: 'function_call_output', call_id: 'c1', output: 'done' }]);
});

it('buildTools wraps functions and passes builtins through', () => {
  const webSearch = { type: 'web_search_preview' };
  const out = buildTools([
    {
      type: 'function',
      name: 'T',
      description: 'd',
      parameters: { type: 'object' },
    },
    webSearch,
  ]);
  expect(out[0]).toEqual({
    type: 'function',
    name: 'T',
    description: 'd',
    parameters: { type: 'object' },
    strict: null,
  });
  // Hosted built-ins pass through untouched — same object, no wrapping.
  expect(out[1]).toBe(webSearch);
});

it('buildTools empty', () => {
  expect(buildTools(null)).toEqual([]);
  expect(buildTools([])).toEqual([]);
});

it('buildTools passes an explicit-null type through untouched', () => {
  // Python dict.get("type", "function") defaults ONLY when the key is
  // absent: `{type: null}` is not "function", so the object passes through
  // as a provider built-in — same reference, extra keys preserved — instead
  // of being rewrapped (and stripped) as a nameless function tool.
  const nulled = { type: null, id: 'ws1', search_context_size: 'high' };
  const out = buildTools([nulled]);
  expect(out[0]).toBe(nulled);
});

it('buildTools forwards explicit-null name and description', () => {
  // Present-but-null values go out as null on the wire (Python emits
  // `"name": null`); ABSENT keys still default.
  const out = buildTools([{ type: 'function', name: null, description: null }]);
  expect(out[0]).toEqual({
    type: 'function',
    name: null,
    description: null,
    parameters: null,
    strict: null,
  });

  const defaulted = buildTools([{ type: 'function' }]);
  expect(defaulted[0]).toEqual({
    type: 'function',
    name: '',
    description: '',
    parameters: null,
    strict: null,
  });
});
