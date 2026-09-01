/**
 * Wire mappers for Chat Completions (`chat.completions.create`).
 *
 * Fills `messages` — including the `role: "system"` entry — and the nested
 * tool definitions. This is the one dialect that carries `cache_control`
 * breakpoints (the only path where LiteLLM forwards them to the backend), so
 * the cache-rendering machinery lives here too. All output keys are provider
 * wire fields and stay snake_case.
 */

import type { Message, SystemBlock } from '../schemas.js';
import { systemText } from '../schemas.js';

/** The `cache_control` ephemeral breakpoint payload. */
export interface CacheControl {
  type: 'ephemeral';
  ttl?: '1h';
}

/** One content block of a Chat Completions message. */
export interface ChatContentPart {
  type: string;
  text?: string;
  image_url?: { url: string };
  cache_control?: CacheControl;
}

/** A nested function tool-call entry on an assistant message. */
export interface ChatCompletionsToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}

/** One Chat Completions wire message. */
export interface ChatCompletionsMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | ChatContentPart[] | null;
  tool_calls?: ChatCompletionsToolCall[];
  tool_call_id?: string;
}

/** A function tool definition in the Chat Completions nested shape. */
export interface ChatCompletionsTool {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

/**
 * Build the `cache_control` ephemeral breakpoint for the given tier.
 *
 * The **bare** `{"type": "ephemeral"}` (default 5-minute TTL) is the form
 * Bedrock (and Anthropic) accept everywhere. The explicit `ttl` field is
 * Anthropic's *extended-cache beta* (1-hour) and is NOT understood by
 * Bedrock — sending it makes the proxy ignore the whole `cache_control`, so
 * nothing caches. Only emit `ttl` for the explicit `"1h"` extended tier;
 * every other value (`"5m"`/default) uses the bare form. Returns a new
 * object each call — callees attach it to mutable message objects.
 */
export function cacheControl(ttl: string): CacheControl {
  const cc: CacheControl = { type: 'ephemeral' };
  if (ttl === '1h') {
    cc.ttl = '1h';
  }
  return cc;
}

/**
 * Render {@link SystemBlock}s as Chat Completions content blocks, attaching
 * a `cache_control` ephemeral breakpoint to each block flagged `cache: true`.
 *
 * Chat-only by construction: the adapter routes every caching request to
 * `chat.completions` (the one path where LiteLLM forwards `cache_control` to
 * the backend), so there is no Responses variant of this shape.
 */
export function renderCacheBlocks(blocks: SystemBlock[], ttl: string): ChatContentPart[] {
  const out: ChatContentPart[] = [];
  for (const b of blocks) {
    const block: ChatContentPart = { type: 'text', text: b.text };
    if (b.cache) {
      block.cache_control = cacheControl(ttl);
    }
    out.push(block);
  }
  return out;
}

/**
 * Render the `role: "system"` message.
 *
 * With `cacheTtl` set and at least one cache-flagged block, content is
 * rendered as `cache_control` content blocks ({@link renderCacheBlocks});
 * otherwise the blocks join to plain text — the shape every backend accepts,
 * and the one OpenAI (which rejects `cache_control` and caches automatically
 * by prefix) requires. The caller owns the policy — pass `null` for models
 * where `cache_control` doesn't apply.
 */
export function buildSystemMessage(
  blocks: SystemBlock[],
  cacheTtl?: string | null,
): ChatCompletionsMessage {
  const content =
    cacheTtl && blocks.some((b) => b.cache)
      ? renderCacheBlocks(blocks, cacheTtl)
      : systemText(blocks);
  return { role: 'system', content };
}

/**
 * Attach a `cache_control` breakpoint to the last message that can carry
 * one, so the whole request prefix above it caches at `ttl`.
 *
 * Caching is prefix-based: a breakpoint on the final message caches
 * everything before it (including the system message the adapter prepends —
 * cached at its own, longer tier; longest-TTL-first ordering is what
 * Anthropic requires when tiers are mixed). On the next turn that message
 * sits mid-history, so the request reads the cached prefix and writes a
 * fresh breakpoint at its own last message — the breakpoint "moves" with
 * the conversation, giving incremental history caching across the short
 * `ttl` window (and across the loop's tool-use rounds within a turn).
 *
 * The provider wants `cache_control` on a content *block*, so a plain
 * string `content` is promoted to a one-block list. No-op if no message can
 * carry it (e.g. a trailing tool-call turn whose `content` is `null`).
 */
function attachMessageCache(items: ChatCompletionsMessage[], ttl: string): void {
  for (let i = items.length - 1; i >= 0; i--) {
    const msg = items[i]!;
    const content = msg.content;
    if (typeof content === 'string') {
      if (!content) {
        continue;
      }
      msg.content = [{ type: 'text', text: content, cache_control: cacheControl(ttl) }];
    } else if (Array.isArray(content) && content.length > 0) {
      const last = content[content.length - 1];
      if (typeof last !== 'object' || last === null || Array.isArray(last)) {
        continue;
      }
      last.cache_control = cacheControl(ttl);
    } else {
      continue;
    }
    return;
  }
}

/**
 * Translate a normalised {@link Message} list into Chat Completions
 * messages.
 *
 * The counterpart of the Responses mapper's `buildInput`. Two differences
 * matter for tool use:
 *
 * - an assistant turn's text and its tool calls live in a **single** message
 *   (`content` + `tool_calls`), not separate items as on Responses; and
 * - a tool result is a `role: "tool"` message keyed by `tool_call_id` (vs a
 *   `function_call_output` item).
 *
 * Tool-call ids pass through verbatim, so each `tool` message pairs with the
 * assistant `tool_calls[].id` that produced it — the provider matches them
 * by that id regardless of which API minted it.
 *
 * `cacheTtl`, when set, attaches a `cache_control` breakpoint to the last
 * message so the conversation prefix caches at that tier (see
 * {@link attachMessageCache}). The caller owns the policy — pass `null` for
 * models that don't support `cache_control`.
 */
export function buildMessages(
  messages: Message[],
  cacheTtl?: string | null,
): ChatCompletionsMessage[] {
  const items: ChatCompletionsMessage[] = [];
  for (const msg of messages) {
    if (msg.role === 'user') {
      if (msg.images && msg.images.length > 0) {
        const parts: ChatContentPart[] = [{ type: 'text', text: msg.content ?? '' }];
        for (const img of msg.images) {
          parts.push({
            type: 'image_url',
            image_url: { url: `data:${img.mimeType};base64,${img.base64}` },
          });
        }
        items.push({ role: 'user', content: parts });
      } else {
        items.push({ role: 'user', content: msg.content ?? '' });
      }
    } else if (msg.role === 'assistant') {
      if (msg.toolCalls && msg.toolCalls.length > 0) {
        // A tool-calls-only turn has no text: send `content: null` (not "")
        // so Bedrock doesn't reject an empty assistant turn.
        items.push({
          role: 'assistant',
          content: msg.content || null,
          tool_calls: msg.toolCalls.map((tc) => ({
            id: tc.id,
            type: 'function' as const,
            function: { name: tc.name, arguments: tc.arguments },
          })),
        });
      } else {
        items.push({ role: 'assistant', content: msg.content ?? '' });
      }
    } else if (msg.role === 'tool') {
      items.push({
        role: 'tool',
        tool_call_id: msg.toolCallId ?? '',
        content: msg.content ?? '',
      });
    }
  }

  if (cacheTtl) {
    attachMessageCache(items, cacheTtl);
  }
  return items;
}

/**
 * Translate function tool definitions into Chat Completions tool shape.
 *
 * The counterpart of the Responses mapper's `buildTools`: each function tool
 * nests its schema under a `"function"` key (vs flat on Responses). Provider
 * built-ins (e.g. `web_search_preview`) aren't supported on this endpoint
 * and are skipped with a warning — the agent reaches web search through a
 * separate function tool on a secondary model, so nothing on this path
 * relies on a hosted tool. A missing (or empty) `parameters` becomes an
 * empty object schema, which the API requires.
 */
export function buildTools(tools?: Record<string, unknown>[] | null): ChatCompletionsTool[] {
  if (!tools || tools.length === 0) {
    return [];
  }
  const result: ChatCompletionsTool[] = [];
  for (const tool of tools) {
    // Python `tool.get("type", "function")`: the default applies ONLY when
    // the key is absent. A key present with an explicit null is NOT a
    // function tool — it fails the check below and is dropped with the
    // warning, exactly like any other non-function type.
    const type = 'type' in tool ? tool['type'] : 'function';
    if (type !== 'function') {
      // Dropping a hosted tool silently would look to the caller like the
      // model simply never used it — make the loss visible.
      console.warn(
        `Dropping non-function tool ${JSON.stringify(type)}: hosted tools are not ` +
          'supported on the chat.completions path',
      );
      continue;
    }
    const params = tool['parameters'] as Record<string, unknown> | null | undefined;
    result.push({
      type: 'function',
      function: {
        // dict.get semantics again: a present-but-null name/description is
        // forwarded as null on the wire, not coerced to ''.
        name: ('name' in tool ? tool['name'] : '') as string,
        description: ('description' in tool ? tool['description'] : '') as string,
        parameters:
          params && Object.keys(params).length > 0
            ? params
            : { type: 'object', properties: {} },
      },
    });
  }
  return result;
}
