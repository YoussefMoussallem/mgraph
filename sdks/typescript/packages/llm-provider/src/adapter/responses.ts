/**
 * The Responses API call paths (`responses.create`).
 *
 * The default dialect: hosted web search and utility streaming run here, and
 * {@link generate} serves non-streaming single shots. Caching never happens
 * on this endpoint — the facade routes every request with caching intent to
 * the chatCompletions path — so the system prompt always rides as plain-text
 * `instructions`.
 */

import type OpenAI from 'openai';
import type {
  ResponseCreateParamsNonStreaming,
  ResponseCreateParamsStreaming,
} from 'openai/resources/responses/responses';

import { generation as langfuseGeneration } from '@genai-sdk/langfuse-client';
import type { TraceAttrs } from '@genai-sdk/langfuse-client';

import {
  asInt,
  costDetails,
  type CostFn,
  extractCacheTokens,
  requestTraceAttributes,
  systemBlocks,
  toProviderError,
  translateProviderErrors,
} from './common.js';
import { buildInput, buildTools } from '../mappers/responses.js';
import { StreamEvent, systemText } from '../schemas.js';
import type { ChatRequest, SystemBlock } from '../schemas.js';

/**
 * Event types the SDK emits that map to nothing the UI needs to render —
 * matched and silently dropped. Anything not handled and not listed here is
 * logged at debug so SDK upgrades surface new event types without breaking
 * callers.
 */
const IGNORED_EVENTS = new Set([
  'response.created',
  'response.in_progress',
  'response.output_item.done',
  'response.content_part.added',
  'response.content_part.done',
  'response.output_text.done',
  // Citations are read from the final response object in the
  // response.completed case, not streamed per-token.
  'response.output_text.annotation.added',
  'response.reasoning_text.done',
  'response.reasoning_summary_text.done',
  'response.reasoning_summary_part.added',
  'response.reasoning_summary_part.done',
  'response.queued',
]);

/**
 * Collect deduped `url_citation` annotations from a final Responses object —
 * the sources a `web_search` call cited.
 *
 * Returns `[{url, title}]` in first-seen order (first-seen title wins; a
 * duplicate URL is dropped entirely); empty when the response carried none.
 * Defensive throughout: the SDK shape varies by model/version and a missing
 * field must degrade to "no sources", never throw mid-stream.
 */
function extractUrlCitations(response: any): Array<{ url: string; title: string }> {
  const seen = new Set<string>();
  const out: Array<{ url: string; title: string }> = [];
  for (const item of response?.output ?? []) {
    if (item?.type !== 'message') {
      continue;
    }
    for (const block of item?.content ?? []) {
      for (const ann of block?.annotations ?? []) {
        if (ann?.type !== 'url_citation') {
          continue;
        }
        const url = ann?.url;
        if (url && !seen.has(url)) {
          seen.add(url);
          out.push({ url, title: ann?.title || '' });
        }
      }
    }
  }
  return out;
}

/**
 * Stream over `responses.create` as normalised {@link StreamEvent}s.
 *
 * The Responses API emits many low-level event types; this collapses them
 * into the stable shape the rest of the codebase consumes (`text_delta`,
 * `thinking_delta`, `tool_call_*`, `web_search_*`, `error`, `done` — event
 * names and data keys snake_case, a wire contract shared with the Python
 * SDK). Unknown event types are logged at debug and ignored so SDK upgrades
 * don't break callers.
 *
 * The Langfuse observation is closed in `finally`, which runs on success, on
 * provider errors, and on early termination by the consumer alike (a
 * `break`/disconnect triggers the generator's `return()`); the terminal
 * `done` event is yielded after it, so an abandoned or failed stream never
 * emits `done`. A terminal event without usage degrades to zero counts
 * instead of throwing mid-stream.
 *
 * Function-call arguments are streamed as tokens; they are tracked by
 * `item_id` (which is opaque and stable within one response) so each delta
 * can be attributed to the right logical `call_id`.
 */
export async function* stream(
  client: OpenAI,
  request: ChatRequest,
  blocks: SystemBlock[],
  opts: { reasoningEffort: string; costFn?: CostFn | null },
): AsyncGenerator<StreamEvent> {
  let inputTokens = 0;
  let outputTokens = 0;
  let cacheRead = 0;
  let cacheWrite = 0;
  // Normalised finish signal for the terminal `done`; stays null when the
  // stream ends without any terminal event — "completion unconfirmed".
  let stopReason: string | null = null;
  const textParts: string[] = [];
  // item_id -> [call_id, name]: item_id is the SDK's transient handle,
  // call_id is the durable identifier the rest of the app references.
  // Map preserves insertion order for the Langfuse tool_calls metadata.
  const funcCalls = new Map<string, [string, string]>();

  // Terminal-response bookkeeping shared by response.completed and
  // response.incomplete: usage rides only the terminal event (an incomplete
  // response still carries the usage it burned — dropping it would make
  // truncation look free), and web_search citations live on the final
  // response's output annotations.
  const readTerminalResponse = (response: any): void => {
    const usage = response?.usage ?? null;
    if (usage != null) {
      inputTokens = asInt(usage.input_tokens ?? 0);
      outputTokens = asInt(usage.output_tokens ?? 0);
      [cacheRead, cacheWrite] = extractCacheTokens(usage);
    }
  };

  const builtTools = buildTools(request.tools);
  const inputItems = buildInput(request.messages);
  // Only cache-less prompts reach this path (caching intent routes to
  // chat.completions), so the system prompt rides in `instructions` as
  // plain text.
  const instructions = systemText(blocks);
  const kwargs: Record<string, unknown> = {
    model: request.model,
    instructions,
    input: inputItems,
    stream: true,
  };
  if (builtTools.length > 0) {
    kwargs['tools'] = builtTools;
  }
  // Without this the provider applies a low default and truncates large
  // tool calls (e.g. a big WriteArtifact) mid-arguments.
  if (request.maxOutputTokens != null) {
    kwargs['max_output_tokens'] = request.maxOutputTokens;
  }

  if (request.thinking) {
    kwargs['reasoning'] = {
      // Per-request effort wins over the adapter's constructor default.
      effort: request.reasoningEffort ?? opts.reasoningEffort,
      summary: 'auto',
    };
  }

  const { stream: _stream, ...traceInput } = kwargs;
  const genObs = langfuseGeneration(
    'llm-stream',
    request.model,
    traceInput,
    requestTraceAttributes(request) as TraceAttrs,
  );

  // The exception (if any) propagating out of the try below — recorded on
  // the observation at close (Python: `gen_ctx.__exit__(*sys.exc_info())`).
  // Consumer abandonment runs only the finally, so it stays a clean close.
  let failure: unknown;

  try {
    try {
      const streamResp: AsyncIterable<any> = await client.responses.create(
        kwargs as unknown as ResponseCreateParamsStreaming,
        request.signal ? { signal: request.signal } : undefined,
      );

      for await (const event of streamResp) {
        switch (event.type) {
          case 'response.output_text.delta':
            if (event.delta) {
              textParts.push(event.delta);
              yield new StreamEvent('text_delta', { text: event.delta });
            }
            break;

          case 'response.reasoning_text.delta':
            if (event.delta) {
              yield new StreamEvent('thinking_delta', { text: event.delta });
            }
            break;

          case 'response.reasoning_summary_text.delta':
            // Reasoning summaries stream on a separate channel from raw
            // reasoning text; both surface to the UI as "thinking" so the
            // caller doesn't need to care.
            if (event.delta) {
              yield new StreamEvent('thinking_delta', { text: event.delta });
            }
            break;

          case 'response.output_item.added': {
            const item = event.item;
            if (item?.type === 'function_call') {
              funcCalls.set(item.id, [item.call_id, item.name]);
              yield new StreamEvent('tool_call_start', {
                call_id: item.call_id,
                name: item.name,
              });
            }
            break;
          }

          case 'response.function_call_arguments.delta': {
            const known = funcCalls.get(event.item_id);
            if (event.delta && known) {
              yield new StreamEvent('tool_call_delta', {
                call_id: known[0],
                delta: event.delta,
              });
            }
            break;
          }

          case 'response.function_call_arguments.done': {
            const known = funcCalls.get(event.item_id);
            if (known) {
              const [callId, name] = known;
              yield new StreamEvent('tool_call_done', {
                call_id: callId,
                name,
                arguments: event.arguments,
              });
            }
            break;
          }

          case 'response.web_search_call.in_progress':
            yield new StreamEvent('web_search_start', {});
            break;

          case 'response.web_search_call.searching':
            yield new StreamEvent('web_search_searching', {});
            break;

          case 'response.web_search_call.completed':
            yield new StreamEvent('web_search_done', {});
            break;

          case 'response.completed': {
            // Usage is only available on the terminal event; cache it for
            // the final `done` envelope. Guarded — including against a
            // terminal event whose `response` is itself null/undefined
            // (Python: `getattr(None, "usage", None)` degrades too) — so a
            // proxy that strips the body degrades to zero counts instead
            // of a throw mid-stream.
            readTerminalResponse(event.response);
            // A completed turn either ended naturally or stopped to call
            // the function tools it opened — the same distinction Anthropic
            // draws between end_turn and tool_use.
            stopReason = funcCalls.size > 0 ? 'tool_use' : 'end_turn';
            // web_search citations live on the final response's output
            // annotations; surface them so callers (e.g. the WebSearch
            // tool) can return the sources searched.
            const sources = extractUrlCitations(event.response);
            if (sources.length > 0) {
              yield new StreamEvent('web_search_sources', { sources });
            }
            break;
          }

          case 'response.incomplete': {
            // The provider stopped early — most commonly the
            // max_output_tokens cap. This is a TERMINAL event: it carries
            // the usage actually burned, and before this case existed the
            // stream ended as a clean zero-usage `done`, making truncation
            // indistinguishable from success (and free). Surface the real
            // usage plus a truthful stop_reason so consumers can run their
            // truncation protocol and bill the call.
            readTerminalResponse(event.response);
            const reason = event.response?.incomplete_details?.reason;
            stopReason = reason === 'max_output_tokens' ? 'max_tokens' : (reason ?? 'incomplete');
            const sources = extractUrlCitations(event.response);
            if (sources.length > 0) {
              yield new StreamEvent('web_search_sources', { sources });
            }
            break;
          }

          case 'response.failed': {
            // Provider reported a soft failure mid-stream — surface the
            // message but don't throw, so partial output is still
            // preserved for the caller. Usage (when the failed response
            // carries it) and the `error` stop_reason still reach the
            // terminal `done`, so a failed call is never billed as a clean
            // zero-usage completion.
            readTerminalResponse(event.response);
            stopReason = 'error';
            let err: unknown = 'Unknown error';
            const resp = event.response;
            if (resp) {
              const errorObj = resp.error;
              if (errorObj) {
                // Python: getattr(error_obj, "message", str(error_obj)) —
                // the fallback applies only when `message` is ABSENT. A
                // present-but-null message is forwarded as null (the wire
                // contract), never replaced with a string rendering.
                err =
                  typeof errorObj === 'object' && 'message' in errorObj
                    ? errorObj.message
                    : String(errorObj);
              }
            }
            yield new StreamEvent('error', { message: err });
            break;
          }

          case 'error': {
            // Standalone error item in the event stream (the Responses API
            // emits these for e.g. upstream provider hiccups) — previously
            // it fell through to the unhandled-event debug log and was
            // silently dropped. Same soft-failure contract as
            // response.failed: surface, don't throw.
            stopReason = 'error';
            yield new StreamEvent('error', {
              message: 'message' in event ? event.message : String(event),
            });
            break;
          }

          default:
            if (!IGNORED_EVENTS.has(event.type)) {
              console.debug('Unhandled stream event: %s', event.type);
            }
        }
      }
    } catch (err) {
      throw toProviderError(err) ?? err;
    }

    // Reaching here means the stream completed cleanly (translated errors
    // threw out of the catch above). Record the aggregated output + token
    // usage; guarded because tracing must never take down a successful
    // request.
    if (genObs) {
      try {
        const usageDetails = {
          input: inputTokens,
          output: outputTokens,
          // Langfuse's Anthropic-style keys so cache pricing and
          // hit-rate dashboards work out of the box.
          cache_read_input_tokens: cacheRead,
          cache_creation_input_tokens: cacheWrite,
        };
        const updateData: Record<string, unknown> = {
          output: textParts.join(''),
          usageDetails,
        };
        if (funcCalls.size > 0) {
          // The tool calls this turn made — visible on the trace without
          // digging through raw output.
          updateData['metadata'] = {
            tool_calls: [...funcCalls.values()].map(([callId, name]) => ({
              call_id: callId,
              name,
            })),
          };
        }
        const costs = costDetails(opts.costFn, request.model, usageDetails);
        if (costs) {
          updateData['costDetails'] = costs;
        }
        genObs.update(updateData as Parameters<typeof genObs.update>[0]);
      } catch (err) {
        console.debug('Langfuse generation update failed', err);
      }
    }
  } catch (err) {
    failure = err;
    throw err;
  } finally {
    // Runs on success, provider errors, AND consumer abandonment (the
    // generator's return()) — the observation must never leak, and a
    // propagating error is recorded on it.
    genObs?.end(failure);
  }

  yield new StreamEvent('done', {
    usage: {
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      cache_read_tokens: cacheRead,
      cache_write_tokens: cacheWrite,
    },
    stop_reason: stopReason,
  });
}

/**
 * Single non-streaming `responses.create` call — assembled text out.
 *
 * Only the `output_text` parts of `message` items are concatenated; tool
 * calls, reasoning, and other output types are dropped. This path never
 * caches — cache flags are ignored here; callers that want caching use the
 * streaming or chat-completion paths. `maxOutputTokens` is forwarded when
 * set (key omitted entirely when unset). The Langfuse observation is closed
 * on every path — tracing must never leak on failures.
 */
export async function generate(
  client: OpenAI,
  request: ChatRequest,
  systemPrompt: string | SystemBlock[] = '',
  opts: { reasoningEffort: string; costFn?: CostFn | null },
): Promise<string> {
  const builtTools = buildTools(request.tools);
  const inputItems = buildInput(request.messages);
  const instructions = systemText(systemBlocks(systemPrompt));
  const kwargs: Record<string, unknown> = {
    model: request.model,
    instructions,
    input: inputItems,
  };
  if (builtTools.length > 0) {
    kwargs['tools'] = builtTools;
  }
  // Same cap as the streaming paths — without it the provider's low default
  // silently truncates long generations.
  if (request.maxOutputTokens != null) {
    kwargs['max_output_tokens'] = request.maxOutputTokens;
  }
  if (request.thinking) {
    kwargs['reasoning'] = {
      // Per-request effort wins over the adapter's constructor default.
      effort: request.reasoningEffort ?? opts.reasoningEffort,
      summary: 'auto',
    };
  }

  const genObs = langfuseGeneration(
    'llm-generate',
    request.model,
    { ...kwargs },
    requestTraceAttributes(request) as TraceAttrs,
  );

  let response: any;
  try {
    response = await translateProviderErrors(() =>
      client.responses.create(
        kwargs as unknown as ResponseCreateParamsNonStreaming,
        request.signal ? { signal: request.signal } : undefined,
      ),
    );
  } catch (err) {
    // Close the observation WITH the propagating error — tracing must never
    // leak on failures, and the failure must be recorded on the span
    // (Python: `gen_ctx.__exit__(type(e), e, e.__traceback__)`).
    genObs?.end(err);
    throw err;
  }

  const parts: string[] = [];
  for (const item of response.output) {
    if (item?.type === 'message') {
      for (const block of item.content) {
        if (block?.type === 'output_text') {
          parts.push(block.text);
        }
      }
    }
  }
  const result = parts.join('');

  if (genObs) {
    try {
      const usage = response.usage ?? null;
      const usageDetails = {
        // Python: getattr(usage, key, 0) — the 0 default applies only
        // when the field is ABSENT; a present-but-null value is
        // forwarded as-is (Langfuse then treats it as unset rather than
        // an asserted zero count).
        input: (usage != null && typeof usage === 'object' && 'input_tokens' in usage
          ? usage.input_tokens
          : 0) as number,
        output: (usage != null && typeof usage === 'object' && 'output_tokens' in usage
          ? usage.output_tokens
          : 0) as number,
      };
      const updatePayload: Record<string, unknown> = {
        output: result,
        usageDetails,
      };
      const costs = costDetails(opts.costFn, request.model, usageDetails);
      if (costs) {
        updatePayload['costDetails'] = costs;
      }
      genObs.update(updatePayload as Parameters<typeof genObs.update>[0]);
    } catch (err) {
      console.debug('Langfuse generation update failed', err);
    }
  }
  genObs?.end();

  return result;
}
