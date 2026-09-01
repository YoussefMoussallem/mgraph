/**
 * Cache plumbing guards.
 * TS twin of Python `tests/test_cache_blocks.py`.
 *
 * The typed-cache contract: a plain-string system prompt renders to the
 * exact bytes with no breakpoints, and cache-flagged `SystemBlock`s become
 * `cache_control` content blocks precisely where flagged. Key ABSENCE is
 * part of the contract — unflagged blocks carry no `cache_control` key at
 * all — so shape assertions use `toStrictEqual` / `in` checks.
 */

import { expect, it } from 'vitest';

import { supportsCacheControl, systemBlocks, wantsCache } from '../src/adapter/common.js';
import {
  buildMessages,
  buildSystemMessage,
  cacheControl,
  renderCacheBlocks,
  type ChatContentPart,
} from '../src/mappers/chatCompletions.js';
import { systemText } from '../src/schemas.js';
import type { Message, SystemBlock } from '../src/schemas.js';

it('str normalises to single uncached block', () => {
  const blocks = systemBlocks('plain prompt');
  // `cache` defaults at read sites (`?? false`) — the TS twin of pydantic's
  // `cache: bool = False`.
  expect(blocks.map((b) => [b.text, b.cache ?? false])).toEqual([['plain prompt', false]]);
  expect(wantsCache(blocks)).toBe(false);
});

it('empty prompts normalise to no blocks', () => {
  expect(systemBlocks('')).toStrictEqual([]);
  expect(systemBlocks([])).toStrictEqual([]);
  expect(systemBlocks([{ text: '', cache: true }])).toStrictEqual([]);
});

it('block list passes through and drops empties', () => {
  const blocks = systemBlocks([
    { text: 'static', cache: true },
    { text: '' },
    { text: 'tail' },
  ]);
  expect(blocks.map((b) => [b.text, b.cache ?? false])).toEqual([
    ['static', true],
    ['tail', false],
  ]);
  expect(wantsCache(blocks)).toBe(true);
});

it('system text joins and ignores flags', () => {
  const blocks = systemBlocks([{ text: 'a', cache: true }, { text: 'b' }]);
  expect(systemText(blocks)).toBe('ab');
});

it('build system message renders blocks when ttl and flagged', () => {
  const blocks: SystemBlock[] = [{ text: 'static', cache: true }, { text: 'tail' }];
  expect(buildSystemMessage(blocks, '5m')).toStrictEqual({
    role: 'system',
    content: [
      { type: 'text', text: 'static', cache_control: { type: 'ephemeral' } },
      { type: 'text', text: 'tail' },
    ],
  });
});

it('build system message plain text without ttl', () => {
  // No cacheTtl is the caller saying "this model can't take cache_control"
  // — flags are ignored and the exact joined bytes go out.
  const blocks: SystemBlock[] = [{ text: 'static', cache: true }, { text: 'tail' }];
  expect(buildSystemMessage(blocks)).toStrictEqual({ role: 'system', content: 'statictail' });
});

it('build system message plain text when nothing flagged', () => {
  const blocks: SystemBlock[] = [{ text: 'just a prompt' }];
  expect(buildSystemMessage(blocks, '5m')).toStrictEqual({
    role: 'system',
    content: 'just a prompt',
  });
});

it('cache control tiers', () => {
  // Bare form (default 5m) is what Bedrock accepts everywhere; the explicit
  // ttl field is the 1h extended-cache beta only.
  expect(cacheControl('5m')).toStrictEqual({ type: 'ephemeral' });
  expect(cacheControl('1h')).toStrictEqual({ type: 'ephemeral', ttl: '1h' });
  // Unknown tiers degrade to the bare form rather than a rejected request.
  expect(cacheControl('2h')).toStrictEqual({ type: 'ephemeral' });
});

it('render attaches breakpoints only where flagged', () => {
  const blocks: SystemBlock[] = [{ text: 'static', cache: true }, { text: 'tail' }];
  expect(renderCacheBlocks(blocks, '1h')).toStrictEqual([
    {
      type: 'text',
      text: 'static',
      cache_control: { type: 'ephemeral', ttl: '1h' },
    },
    { type: 'text', text: 'tail' },
  ]);
});

it('render supports multiple breakpoints', () => {
  // e.g. one breakpoint after static rules, one after a template appendix.
  const blocks: SystemBlock[] = [
    { text: 'rules', cache: true },
    { text: 'appendix', cache: true },
    { text: 'volatile' },
  ];
  const out = renderCacheBlocks(blocks, '5m');
  expect(out.map((b) => 'cache_control' in b)).toEqual([true, true, false]);
});

it('supports cache control families', () => {
  expect(supportsCacheControl('us.anthropic.claude-opus-4-1')).toBe(true);
  expect(supportsCacheControl('claude-sonnet-5')).toBe(true);
  expect(supportsCacheControl('gemini-2.5-pro')).toBe(true);
  // OpenAI caches automatically by prefix and rejects cache_control blocks.
  expect(supportsCacheControl('gpt-4o')).toBe(false);
  expect(supportsCacheControl('')).toBe(false);
});

it('message cache promotes last string message', () => {
  const out = buildMessages([{ role: 'user', content: 'hi' }], '5m');
  expect(out[out.length - 1]!.content).toStrictEqual([
    { type: 'text', text: 'hi', cache_control: { type: 'ephemeral' } },
  ]);
});

it('message cache attaches to last part of multipart content', () => {
  // Vision turns carry list content; the breakpoint rides the LAST part.
  const messages: Message[] = [
    {
      role: 'user',
      content: 'look',
      images: [{ mimeType: 'image/png', base64: 'QUJD' }],
    },
  ];
  const out = buildMessages(messages, '5m');
  const content = out[out.length - 1]!.content as ChatContentPart[];
  expect(content[content.length - 1]!.cache_control).toStrictEqual({ type: 'ephemeral' });
  expect('cache_control' in content[0]!).toBe(false);
});

it('message cache skips back past uncacheable tail', () => {
  // A trailing tool-calls-only turn (content null) can't carry the
  // breakpoint — it lands on the nearest earlier message that can.
  const messages: Message[] = [
    { role: 'user', content: 'hi' },
    {
      role: 'assistant',
      toolCalls: [{ id: 'c1', name: 'T', arguments: '{}' }],
    },
  ];
  const out = buildMessages(messages, '5m');
  expect(out[out.length - 1]!.content).toBeNull();
  const first = out[0]!.content as ChatContentPart[];
  expect(first[first.length - 1]!.cache_control).toStrictEqual({ type: 'ephemeral' });
});

it('message cache noop when no message can carry it', () => {
  const messages: Message[] = [
    {
      role: 'assistant',
      toolCalls: [{ id: 'c1', name: 'T', arguments: '{}' }],
    },
  ];
  const out = buildMessages(messages, '5m');
  expect(out[out.length - 1]!.content).toBeNull();
});

it('no cache ttl leaves messages untouched', () => {
  const out = buildMessages([{ role: 'user', content: 'hi' }]);
  expect(out).toStrictEqual([{ role: 'user', content: 'hi' }]);
});
