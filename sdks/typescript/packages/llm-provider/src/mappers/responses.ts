/**
 * Wire mappers for the Responses API (`responses.create`).
 *
 * Fills the `input` array and the flat tool definitions. No system-prompt
 * shape lives here — on this endpoint it rides as the plain-text
 * `instructions` param ({@link systemText}) — and no `cache_control` either:
 * the adapter routes every caching request to the chatCompletions mappers.
 * All emitted keys are provider wire fields and stay snake_case.
 */

import type { Message } from '../schemas.js';

/** Text part of a multipart user message. */
export interface ResponsesInputTextPart {
  type: 'input_text';
  text: string;
}

/** Image part of a multipart user message — a base64 data URL. */
export interface ResponsesInputImagePart {
  type: 'input_image';
  image_url: string;
}

export type ResponsesContentPart = ResponsesInputTextPart | ResponsesInputImagePart;

/** A user/assistant message input item. */
export interface ResponsesMessageItem {
  role: 'user' | 'assistant';
  content: string | ResponsesContentPart[];
}

/** An assistant tool call replayed as its own input item. */
export interface ResponsesFunctionCallItem {
  type: 'function_call';
  call_id: string;
  name: string;
  arguments: string;
}

/** A tool result paired to its originating call by `call_id`. */
export interface ResponsesFunctionCallOutputItem {
  type: 'function_call_output';
  call_id: string;
  output: string;
}

export type ResponsesInputItem =
  | ResponsesMessageItem
  | ResponsesFunctionCallItem
  | ResponsesFunctionCallOutputItem;

/** A custom function tool in the flat Responses shape. */
export interface ResponsesFunctionToolDef {
  type: 'function';
  name: string;
  description: string;
  parameters: Record<string, unknown> | null;
  strict: null;
}

/** Function tools are wrapped; provider built-ins pass through untouched. */
export type ResponsesToolDef = ResponsesFunctionToolDef | Record<string, unknown>;

/**
 * Translate the normalised {@link Message} list into Responses `input` items.
 *
 * Each role maps to a different wire shape:
 *
 * - `user` with no images → a plain `{role, content}` message.
 * - `user` with images → multi-part content (one text part + one image part
 *   per attachment) so the model sees them inline. Images are sent as base64
 *   data URLs to keep requests self-contained.
 * - `assistant` may emit text, tool calls, or both — each is a separate
 *   input item, matching the shape the Responses API would have produced
 *   itself. Emitting text first preserves the original ordering the model
 *   used.
 * - `tool` turns become `function_call_output` items keyed by `call_id` so
 *   the provider can pair them with the originating tool call.
 */
export function buildInput(messages: Message[]): ResponsesInputItem[] {
  const items: ResponsesInputItem[] = [];
  for (const msg of messages) {
    if (msg.role === 'user') {
      if (msg.images && msg.images.length > 0) {
        const parts: ResponsesContentPart[] = [
          {
            type: 'input_text',
            text: msg.content ?? '',
          },
        ];
        for (const img of msg.images) {
          parts.push({
            type: 'input_image',
            image_url: `data:${img.mimeType};base64,${img.base64}`,
          });
        }
        items.push({ role: 'user', content: parts });
      } else {
        items.push({
          role: 'user',
          content: msg.content ?? '',
        });
      }
    } else if (msg.role === 'assistant') {
      if (msg.content) {
        items.push({
          role: 'assistant',
          content: msg.content,
        });
      }
      if (msg.toolCalls) {
        for (const tc of msg.toolCalls) {
          items.push({
            type: 'function_call',
            call_id: tc.id,
            name: tc.name,
            arguments: tc.arguments,
          });
        }
      }
    } else if (msg.role === 'tool') {
      items.push({
        type: 'function_call_output',
        call_id: msg.toolCallId ?? '',
        output: msg.content ?? '',
      });
    }
  }
  return items;
}

/**
 * Translate tool definitions into the flat Responses wire shape.
 *
 * Tools with a `type` other than `"function"` (e.g. `web_search_preview`)
 * are provider built-ins: they don't carry a JSON schema on our side and the
 * API accepts the raw object, so they pass through untouched — the same
 * object reference, no clone. Custom function tools are rewrapped so exactly
 * the expected keys (including an explicit `strict: null`) go out on the
 * wire. `null`/`undefined`/empty input → `[]`.
 */
export function buildTools(tools: Record<string, unknown>[] | null | undefined): ResponsesToolDef[] {
  if (!tools || tools.length === 0) {
    return [];
  }
  const result: ResponsesToolDef[] = [];
  for (const tool of tools) {
    // Python `tool.get("type", "function")`: the default applies ONLY when
    // the key is absent — a present-but-null type is NOT "function", so the
    // tool passes through untouched as a provider built-in rather than
    // being rewrapped (and stripped) as a function tool.
    const toolType = 'type' in tool ? tool['type'] : 'function';
    if (toolType !== 'function') {
      result.push(tool);
    } else {
      result.push({
        type: 'function',
        // dict.get semantics again: present-but-null name/description are
        // forwarded as null on the wire, not coerced to ''.
        name: ('name' in tool ? tool['name'] : '') as string,
        description: ('description' in tool ? tool['description'] : '') as string,
        parameters: (tool['parameters'] as Record<string, unknown> | null | undefined) ?? null,
        strict: null,
      });
    }
  }
  return result;
}
