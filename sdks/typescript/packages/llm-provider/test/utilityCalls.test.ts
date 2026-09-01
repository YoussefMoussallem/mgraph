/**
 * The non-streaming call shapes: `generate`, `generateChatCompletion`,
 * `generateImage`, `listModels`.
 * TS twin of Python `tests/test_utility_calls.py`.
 *
 * Pins output assembly, the two self-healing parameter retries (temperature,
 * image response_format), cached system blocks on the utility path, and
 * usage normalisation. Fake clients are plain objects injected through the
 * constructor `client` option — the TS twin of Python's monkeypatched
 * `adapter.client`. Python's tuple returns adapt to the PARITY.md shapes:
 * `{ text, usage }` and `{ images, usage }`.
 */

import { expect, it } from 'vitest';
import { APIError } from 'openai';
import type OpenAI from 'openai';

import { LLMAdapter } from '../src/adapter/core.js';
import type { ChatRequest, SystemBlock } from '../src/schemas.js';

/** Python `_status_error`: an SDK status error with `.status` and message. */
function statusError(status: number, message: string): APIError {
  return new APIError(status, undefined, message, undefined);
}

/** Adapter with an injected fake client (Python `_adapter_with`). */
function adapterWith(client: unknown): LLMAdapter {
  return new LLMAdapter({
    apiKey: 'test',
    baseUrl: 'http://localhost/v1',
    client: client as OpenAI,
  });
}

// ----------------------------------------------------------------- generate

it('generate assembles only output_text', async () => {
  const response = {
    output: [
      { type: 'reasoning' }, // non-message items skipped
      {
        type: 'message',
        content: [
          { type: 'output_text', text: 'Hello ' },
          { type: 'refusal', text: 'IGNORED' },
          { type: 'output_text', text: 'world' },
        ],
      },
    ],
    usage: null,
  };
  const captured: Record<string, unknown> = {};

  const create = async (kwargs: Record<string, unknown>) => {
    Object.assign(captured, kwargs);
    return response;
  };

  const adapter = adapterWith({ responses: { create } });
  const req: ChatRequest = {
    model: 'm',
    messages: [{ role: 'user', content: 'q' }],
    thinking: true,
  };

  // Cache flags are ignored on this path — plain joined text goes out.
  const system: SystemBlock[] = [{ text: 'a', cache: true }, { text: 'b' }];
  const result = await adapter.generate(req, system);

  expect(result).toBe('Hello world');
  expect(captured['instructions']).toBe('ab');
  expect(captured['reasoning']).toStrictEqual({ effort: 'medium', summary: 'auto' });
});

// ------------------------------------------------- generateChatCompletion

function chatResponse(content: string) {
  return { choices: [{ message: { content } }], usage: null };
}

it('chat completion returns stripped text', async () => {
  const create = async () => chatResponse('  A Title  ');

  const adapter = adapterWith({ chat: { completions: { create } } });
  const result = await adapter.generateChatCompletion({
    model: 'm',
    systemPrompt: 'sys',
    userContent: 'text',
  });
  expect(result).toBe('A Title');
});

it('chat completion empty on no choices', async () => {
  const create = async () => ({ choices: [], usage: null });

  const adapter = adapterWith({ chat: { completions: { create } } });
  const result = await adapter.generateChatCompletion({
    model: 'm',
    systemPrompt: 'sys',
    userContent: 'text',
  });
  expect(result).toBe('');
});

it('chat completion temperature retry and memo', async () => {
  // The memo lives on the adapter instance, so a fresh adapter starts clean.
  const calls: Record<string, unknown>[] = [];

  const create = async (kwargs: Record<string, unknown>) => {
    // Snapshot: the retry re-sends the same (mutated) params object.
    calls.push({ ...kwargs });
    if ('temperature' in kwargs) {
      throw statusError(400, 'temperature is deprecated for this model');
    }
    return chatResponse('ok');
  };

  const adapter = adapterWith({ chat: { completions: { create } } });

  // First call: 400 on temperature -> dropped -> retried once -> succeeds.
  const result = await adapter.generateChatCompletion({
    model: 'claude-opus-4-7',
    systemPrompt: 's',
    userContent: 'u',
  });
  expect(result).toBe('ok');
  expect('temperature' in calls[0]!).toBe(true);
  expect('temperature' in calls[1]!).toBe(false);

  // Model memoised on the adapter: the next call never sends temperature.
  await adapter.generateChatCompletion({
    model: 'claude-opus-4-7',
    systemPrompt: 's',
    userContent: 'u',
  });
  expect('temperature' in calls[2]!).toBe(false);
  expect(calls.length).toBe(3);
});

it('chat completion renders cached system blocks', async () => {
  const captured: Record<string, unknown> = {};

  const create = async (kwargs: Record<string, unknown>) => {
    Object.assign(captured, kwargs);
    return chatResponse('ok');
  };

  const adapter = adapterWith({ chat: { completions: { create } } });
  const system: SystemBlock[] = [{ text: 'rules', cache: true }, { text: 'tail' }];

  await adapter.generateChatCompletion({
    model: 'claude-x',
    systemPrompt: system,
    userContent: 'u',
    cacheTtl: '5m',
  });
  expect((captured['messages'] as any[])[0].content).toStrictEqual([
    { type: 'text', text: 'rules', cache_control: { type: 'ephemeral' } },
    { type: 'text', text: 'tail' },
  ]);

  // OpenAI model: flags ignored, plain text.
  await adapter.generateChatCompletion({
    model: 'gpt-4o',
    systemPrompt: system,
    userContent: 'u',
  });
  expect((captured['messages'] as any[])[0].content).toBe('rulestail');
});

it('chat completion return usage normalises cache counters', async () => {
  const usage = {
    prompt_tokens: 100,
    completion_tokens: 9,
    prompt_tokens_details: { cached_tokens: 80 },
    cache_creation_input_tokens: 11,
  };

  const create = async () => ({ choices: [{ message: { content: 't' } }], usage });

  const adapter = adapterWith({ chat: { completions: { create } } });
  // Python's `(text, usage)` tuple is `{ text, usage }` here (PARITY.md).
  const { text, usage: out } = await adapter.generateChatCompletion({
    model: 'm',
    systemPrompt: 's',
    userContent: 'u',
    returnUsage: true,
  });
  expect(text).toBe('t');
  expect(out).toStrictEqual({
    input_tokens: 100,
    output_tokens: 9,
    cache_read_tokens: 80,
    cache_write_tokens: 11,
  });
});

// ------------------------------------------------------------ generateImage

function imageResponse(payloads: Array<string | null>, usage: unknown = null) {
  return { data: payloads.map((p) => ({ b64_json: p })), usage };
}

it('generate image decodes bytes and usage', async () => {
  const captured: Record<string, unknown> = {};
  const b64 = Buffer.from('PNGBYTES').toString('base64');

  const generate = async (kwargs: Record<string, unknown>) => {
    Object.assign(captured, kwargs);
    return imageResponse([b64], { input_tokens: 12, output_tokens: 340 });
  };

  const adapter = adapterWith({ images: { generate } });
  // Python's `(images, usage)` tuple is `{ images, usage }` here (PARITY.md).
  const { images, usage } = await adapter.generateImage({ model: 'gpt-image-1', prompt: 'a cat' });

  expect(images).toStrictEqual([Buffer.from('PNGBYTES')]);
  expect(usage).toStrictEqual({ input_tokens: 12, output_tokens: 340 });
  expect(captured['response_format']).toBe('b64_json');
  expect('quality' in captured).toBe(false); // only forwarded when set
});

it('generate image retries without response_format', async () => {
  const calls: Record<string, unknown>[] = [];
  const b64 = Buffer.from('X').toString('base64');

  const generate = async (kwargs: Record<string, unknown>) => {
    // Snapshot: the retry re-sends the same (mutated) params object.
    calls.push({ ...kwargs });
    if ('response_format' in kwargs) {
      throw statusError(400, 'Unknown parameter: response_format');
    }
    return imageResponse([b64]);
  };

  const adapter = adapterWith({ images: { generate } });
  const { images, usage } = await adapter.generateImage({
    model: 'gpt-image-1',
    prompt: 'p',
    quality: 'high',
  });

  expect(images).toStrictEqual([Buffer.from('X')]);
  expect('response_format' in calls[1]!).toBe(false);
  expect(calls[1]!['quality']).toBe('high');
  // Flat-priced models report no usage — zeros, caller prices per image.
  expect(usage).toStrictEqual({ input_tokens: 0, output_tokens: 0 });
});

it('generate image skips undecodable payloads', async () => {
  const generate = async () =>
    imageResponse(['!!!not-base64!!!', Buffer.from('OK').toString('base64'), null]);

  const adapter = adapterWith({ images: { generate } });
  const { images } = await adapter.generateImage({ model: 'm', prompt: 'p' });
  expect(images).toStrictEqual([Buffer.from('OK')]);
});

it('generate image tolerates stray non-alphabet characters in b64', async () => {
  // Python's default base64.b64decode DISCARDS characters outside the
  // standard alphabet before decoding (whitespace, '!', a URL-safe '-'
  // from a nonconforming backend) and only rejects bad padding afterwards.
  // Rejecting the whole payload would silently return fewer images than a
  // successful, billed API call produced. 'QUJDRUZH' decodes to 'ABCEFG'.
  const generate = async () => imageResponse(['QUJD!RU-ZH\n']);

  const adapter = adapterWith({ images: { generate } });
  const { images } = await adapter.generateImage({ model: 'm', prompt: 'p' });
  expect(images).toStrictEqual([Buffer.from('ABCEFG')]);
});

// -------------------------------------------------------------- listModels

it('list models returns trimmed records', async () => {
  const list = async () => ({
    data: [{ id: 'm1', owned_by: 'org', noisy_field: 'x' }],
  });

  const adapter = adapterWith({ models: { list } });
  // API-surface field is camelCase (`ownedBy`) per the naming rules — only
  // stream-event payloads stay snake_case.
  expect(await adapter.listModels()).toStrictEqual([{ id: 'm1', ownedBy: 'org' }]);
});
