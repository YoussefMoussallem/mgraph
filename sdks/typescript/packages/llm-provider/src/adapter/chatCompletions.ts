/**
 * The Chat Completions call paths (`chat.completions.create`).
 *
 * The caching dialect: LiteLLM honors content-block `cache_control` on
 * `/chat/completions` (it strips it during the Responses transform), so the
 * facade routes every request with caching intent to {@link stream} here.
 * {@link generateChatCompletion} serves short utility generations (titles,
 * labels, vision describes) where the Responses API may be unavailable.
 */

import type OpenAI from 'openai';
import { APIError } from 'openai';
import type {
  ChatCompletionCreateParamsNonStreaming,
  ChatCompletionCreateParamsStreaming,
} from 'openai/resources/chat/completions';

import { generation as langfuseGeneration } from '@genai-sdk/langfuse-client';
import type { TraceAttrs } from '@genai-sdk/langfuse-client';

import {
  asInt,
  costDetails,
  type CostFn,
  extractCacheTokens,
  mapFinishReason,
  requestTraceAttributes,
  supportsCacheControl,
  systemBlocks,
  toProviderError,
  traceAttributes,
  translateProviderErrors,
} from './common.js';
import {
  buildMessages,
  buildSystemMessage,
  buildTools,
  type ChatCompletionsMessage,
} from '../mappers/chatCompletions.js';
import { StreamEvent, type ChatRequest, type SystemBlock } from '../schemas.js';

/**
 * Duck-typed streamed tool-call fragment (`delta.tool_calls[]`). `index` is
 * required by the wire contract but typed optional here because chunks are
 * not runtime-validated — the stream loop rejects fragments without one.
 */
interface ToolCallFragment {
  index?: number | null;
  id?: string | null;
  function?: { name?: string | null; arguments?: string | null } | null;
}

/** Duck-typed chunk delta — any of the fields may be absent. */
interface ChunkDelta {
  content?: string | null;
  reasoning_content?: string | null;
  tool_calls?: ToolCallFragment[] | null;
}

interface ChunkChoice {
  delta?: ChunkDelta | null;
  finish_reason?: string | null;
}

/** Duck-typed chat completion chunk — tolerant of proxy variations. */
interface ChatCompletionChunkLike {
  choices?: ChunkChoice[] | null;
  usage?: unknown;
}

/** Duck-typed non-streaming chat completion response. */
interface ChatCompletionLike {
  choices?: Array<{ message?: { content?: string | null } | null } | null> | null;
  usage?: unknown;
}

/** Per-index accumulation state for streamed tool-call fragments. */
interface ToolCallEntry {
  callId: string;
  name: string;
  args: string;
  started: boolean;
  flushed: number;
}

/**
 * Build `tool_call_done` events for any not-yet-closed tool calls.
 *
 * Idempotent via `doneIndices` so the finish_reason path and the trailing
 * flush can both call it without double-emitting. Emits in index order for
 * deterministic multi-tool turns. A call whose identity never arrived (no
 * fragment carried id/name, so no start was emitted) gets its start here so
 * consumers always see a paired start → done.
 */
function toolDones(
  toolCalls: Map<number, ToolCallEntry>,
  doneIndices: Set<number>,
): StreamEvent[] {
  const events: StreamEvent[] = [];
  for (const idx of [...toolCalls.keys()].sort((a, b) => a - b)) {
    if (doneIndices.has(idx)) {
      continue;
    }
    doneIndices.add(idx);
    const entry = toolCalls.get(idx)!;
    if (!entry.started) {
      entry.started = true;
      events.push(
        new StreamEvent('tool_call_start', { call_id: entry.callId, name: entry.name }),
      );
    }
    events.push(
      new StreamEvent('tool_call_done', {
        call_id: entry.callId,
        name: entry.name,
        arguments: entry.args,
      }),
    );
  }
  return events;
}

/**
 * Stream the main agent loop over `chat.completions`.
 *
 * Emits the same normalised {@link StreamEvent}s as the Responses path
 * (`text_delta`, `thinking_delta`, `tool_call_*`, `error`, `done` with
 * cache-token counts). Hosted `web_search_*` events don't occur here — the
 * agent reaches web search through a function tool on a secondary model,
 * which keeps its own Responses path.
 *
 * Tool calls stream differently than on Responses and need care:
 * chat.completions sends `delta.tool_calls` fragments keyed by array
 * **index** (the `id` and function name arrive on the first fragment, the
 * JSON `arguments` stream across the rest), and there is **no** per-call
 * "done" event — completion is signalled by `finish_reason`. Fragments
 * accumulate by index and `tool_call_done` flushes when the finish arrives
 * (plus a trailing flush in case the proxy omits the finish on the
 * usage-only final chunk).
 *
 * The Langfuse observation is ended in `finally`, so it closes on success,
 * on provider errors, AND when the consumer abandons the stream — consume
 * with `for await` (or call `return()` on the iterator) so teardown runs.
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
  // stream ends without any finish_reason — "completion unconfirmed".
  let stopReason: string | null = null;
  const textParts: string[] = [];
  // index -> entry: chat.completions keys streamed tool-call fragments by
  // their position in the array, not an item id. `started`/`flushed` defer
  // start and delta emission until the call's identity (id/name) arrived.
  const toolCalls = new Map<number, ToolCallEntry>();
  const doneIndices = new Set<number>();

  // System prompt first (cache-flagged blocks render as cache_control
  // content — the shape LiteLLM forwards to Bedrock's cachePoint / Gemini
  // context caching), then the conversation. Models that don't take
  // cache_control (OpenAI caches automatically) get plain text.
  const cacheable = supportsCacheControl(request.model);
  const messages: ChatCompletionsMessage[] = [
    buildSystemMessage(blocks, cacheable ? (request.cacheTtl ?? '5m') : null),
  ];
  messages.push(
    ...buildMessages(
      request.messages,
      // Cache the conversation too: a breakpoint on the final message
      // caches the system→history prefix at the shorter message tier.
      cacheable ? (request.messageCacheTtl ?? null) : null,
    ),
  );

  const params: Record<string, unknown> = {
    model: request.model,
    messages,
    stream: true,
    // Without include_usage the stream carries no usage object, so the
    // cache-token counts would never arrive — the whole point here.
    stream_options: { include_usage: true },
  };
  const chatTools = buildTools(request.tools);
  if (chatTools.length > 0) {
    params['tools'] = chatTools;
  }
  // Same cap as the Responses path — without it the provider's low default
  // truncates large tool-call JSON mid-stream (WriteArtifact with big HTML).
  if (request.maxOutputTokens != null) {
    params['max_tokens'] = request.maxOutputTokens;
  }
  if (request.thinking) {
    // LiteLLM maps reasoning_effort -> the provider's thinking budget; the
    // thinking text streams back as `delta.reasoning_content`. Per-request
    // effort wins over the adapter's constructor default.
    params['reasoning_effort'] = request.reasoningEffort ?? opts.reasoningEffort;
  }

  const traceInput = Object.fromEntries(
    Object.entries(params).filter(([k]) => k !== 'stream' && k !== 'stream_options'),
  );
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
      const streamResp = (await client.chat.completions.create(
        params as unknown as ChatCompletionCreateParamsStreaming,
        request.signal ? { signal: request.signal } : undefined,
      )) as AsyncIterable<ChatCompletionChunkLike>;

      for await (const chunk of streamResp) {
        // Usage rides the final chunk (include_usage); `choices` is then
        // typically empty, so read usage before touching choices.
        const usage = chunk.usage;
        if (usage) {
          const u = usage as Record<string, unknown>;
          inputTokens = asInt(u['prompt_tokens']);
          outputTokens = asInt(u['completion_tokens']);
          [cacheRead, cacheWrite] = extractCacheTokens(usage);
        }

        const choices = chunk.choices;
        if (!choices || choices.length === 0) {
          continue;
        }
        const choice = choices[0]!;
        const delta = choice.delta;

        if (delta != null) {
          const content = delta.content;
          if (content) {
            textParts.push(content);
            yield new StreamEvent('text_delta', { text: content });
          }

          const reasoning = delta.reasoning_content;
          if (reasoning) {
            yield new StreamEvent('thinking_delta', { text: reasoning });
          }

          for (const tc of delta.tool_calls ?? []) {
            const idx = tc.index;
            if (typeof idx !== 'number') {
              // Python reads `tc.index` off the typed SDK model, so a
              // fragment without an index fails loudly (untranslated —
              // the finally still closes the observation). Silently keying
              // on `undefined` would merge distinct calls into one phantom
              // entry and emit corrupted-but-normal-looking events.
              throw new TypeError('tool_calls fragment carried no index');
            }
            const fn = tc.function;
            let entry = toolCalls.get(idx);
            if (entry === undefined) {
              entry = { callId: '', name: '', args: '', started: false, flushed: 0 };
              toolCalls.set(idx, entry);
            }
            // id / name can be split across fragments — backfill.
            if (tc.id && !entry.callId) {
              entry.callId = tc.id;
            }
            if (fn?.name && !entry.name) {
              entry.name = fn.name;
            }
            const frag = fn?.arguments;
            if (frag) {
              entry.args += frag;
            }
            // Defer start until the call has an identity: starting with an
            // empty call_id that later fragments backfill would hand
            // consumers mismatched correlation keys.
            if (!entry.started && (entry.callId || entry.name)) {
              entry.started = true;
              yield new StreamEvent('tool_call_start', {
                call_id: entry.callId,
                name: entry.name,
              });
            }
            // Flush any args beyond what's already been emitted — including
            // fragments buffered while identity was pending — as one delta.
            if (entry.started && entry.args.length > entry.flushed) {
              const pending = entry.args.slice(entry.flushed);
              entry.flushed = entry.args.length;
              yield new StreamEvent('tool_call_delta', {
                call_id: entry.callId,
                delta: pending,
              });
            }
          }
        }

        // A finish_reason closes the assistant turn; chat.completions has no
        // per-call done event, so flush accumulated tool calls here. The
        // reason itself is normalised onto the terminal `done` so consumers
        // can tell a clean stop from truncation.
        if (choice.finish_reason) {
          stopReason = mapFinishReason(choice.finish_reason);
          for (const ev of toolDones(toolCalls, doneIndices)) {
            yield ev;
          }
        }
      }
    } catch (err) {
      // SDK errors never leak past the adapter boundary.
      throw toProviderError(err) ?? err;
    }

    // Clean completion (translated errors thrown out above). Trailing flush:
    // if the stream ended without a finish_reason (some proxies omit it on
    // the usage-only final chunk) close any open calls.
    for (const ev of toolDones(toolCalls, doneIndices)) {
      yield ev;
    }

    if (genObs) {
      try {
        const usageDetails = {
          input: inputTokens,
          output: outputTokens,
          // Langfuse's Anthropic-style keys so cache pricing and hit-rate
          // dashboards work out of the box.
          cache_read_input_tokens: cacheRead,
          cache_creation_input_tokens: cacheWrite,
        };
        const updatePayload: Record<string, unknown> = {
          output: textParts.join(''),
          usageDetails,
          ...(toolCalls.size > 0
            ? {
                // The tool calls this turn made — visible on the trace
                // without digging through raw output.
                metadata: {
                  tool_calls: [...toolCalls.entries()]
                    .sort((a, b) => a[0] - b[0])
                    .map(([, e]) => ({ call_id: e.callId, name: e.name })),
                },
              }
            : {}),
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
  } catch (err) {
    failure = err;
    throw err;
  } finally {
    // Runs on success, provider errors, AND consumer abandonment (early
    // `return()` from `for await` break) — the observation must never leak,
    // and a propagating error is recorded on it.
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
 * Normalised usage returned by {@link generateChatCompletion} with
 * `returnUsage: true`. Keys stay snake_case — the same cross-SDK usage
 * contract as the stream's `done` event.
 */
export interface ChatCompletionUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
}

/**
 * Options for {@link generateChatCompletion}.
 *
 * `temperatureUnsupported` is the caller-owned memo of models that rejected
 * `temperature` — mutated in place when a 400 teaches us a new one, so
 * subsequent calls skip the doomed first attempt. Pass the same Set by
 * reference across calls, never a copy.
 *
 * `userId` / `sessionId` / `traceMetadata` / `traceTags` are Langfuse trace
 * attributes — never sent to the provider.
 */
export interface GenerateChatCompletionOptions {
  model: string;
  systemPrompt: string | SystemBlock[];
  userContent: string | Record<string, unknown>[];
  /**
   * Default `0.3`. Pass `null` to OMIT the parameter entirely (the provider's
   * own default applies) — for callers that must not send temperature at all,
   * e.g. models/gateways that reject it and shouldn't burn the drop-and-retry.
   */
  temperature?: number | null;
  temperatureUnsupported: Set<string>;
  /** Optional Langfuse cost pricer — see {@link CostFn}. */
  costFn?: CostFn | null;
  /**
   * Cap on tokens the model may generate (`max_tokens`). When unset the
   * provider's (often low) default applies — which silently truncates long
   * utility generations. Same contract as `ChatRequest.maxOutputTokens`.
   */
  maxOutputTokens?: number | null;
  /** Default `"1h"` (utility prompts are stable; the longer tier pays off). */
  cacheTtl?: string;
  returnUsage?: boolean;
  /**
   * Optional AbortSignal cancelling the HTTP request. Surfaces as the
   * openai SDK's `APIUserAbortError`, never translated into the provider
   * hierarchy — same contract as `ChatRequest.signal`.
   */
  signal?: AbortSignal | null;
  userId?: string | null;
  sessionId?: string | null;
  traceMetadata?: Record<string, unknown> | null;
  traceTags?: string[] | null;
}

/**
 * Single non-streaming Chat Completions call — plain text in/out.
 *
 * Returns the assistant's `content` string (stripped), or empty string if
 * the model returned no text; with `returnUsage: true`, `{ text, usage }`
 * with normalised token counts. Does **not** support tools or reasoning —
 * only system + user messages. Cache-flagged {@link SystemBlock}s become
 * `cache_control` breakpoints on supporting models.
 *
 * Newer Claude models hard-400 on `temperature`; on that signal the param
 * is dropped, the model remembered in `temperatureUnsupported`, and the
 * call retried exactly once. The Langfuse observation closes on every exit
 * path — update failures are logged, never thrown.
 */
export async function generateChatCompletion(
  client: OpenAI,
  opts: GenerateChatCompletionOptions & { returnUsage: true },
): Promise<{ text: string; usage: ChatCompletionUsage }>;
export async function generateChatCompletion(
  client: OpenAI,
  opts: GenerateChatCompletionOptions & { returnUsage?: false },
): Promise<string>;
export async function generateChatCompletion(
  client: OpenAI,
  opts: GenerateChatCompletionOptions,
): Promise<string | { text: string; usage: ChatCompletionUsage }> {
  const blocks = systemBlocks(opts.systemPrompt);
  const params: Record<string, unknown> = {
    model: opts.model,
    messages: [
      buildSystemMessage(
        blocks,
        supportsCacheControl(opts.model) ? (opts.cacheTtl ?? '1h') : null,
      ),
      { role: 'user', content: opts.userContent },
    ],
  };
  if (opts.temperature !== null && !opts.temperatureUnsupported.has(opts.model)) {
    params['temperature'] = opts.temperature ?? 0.3;
  }
  if (opts.maxOutputTokens != null) {
    params['max_tokens'] = opts.maxOutputTokens;
  }

  const genObs = langfuseGeneration(
    'llm-chat-completion',
    opts.model,
    { ...params },
    traceAttributes({
      userId: opts.userId,
      sessionId: opts.sessionId,
      metadata: opts.traceMetadata,
      tags: opts.traceTags,
    }) as TraceAttrs,
  );

  // Propagating error, recorded on the observation at close — Python closes
  // the context with `(type(e), e, e.__traceback__)` on failure.
  let failure: unknown;

  try {
    const requestOptions = opts.signal ? { signal: opts.signal } : undefined;
    const response = await translateProviderErrors(async () => {
      try {
        return (await client.chat.completions.create(
          params as unknown as ChatCompletionCreateParamsNonStreaming,
          requestOptions,
        )) as ChatCompletionLike;
      } catch (first) {
        // Newer Claude models (e.g. Opus 4.x on Bedrock) hard-400 on
        // `temperature`. Drop the param, remember the model, retry once —
        // so a single configured model id works across families and later
        // calls skip the failed attempt entirely.
        if (
          first instanceof APIError &&
          first.status === 400 &&
          'temperature' in params &&
          (first.message ?? '').toLowerCase().includes('temperature')
        ) {
          opts.temperatureUnsupported.add(opts.model);
          delete params['temperature'];
          return (await client.chat.completions.create(
            params as unknown as ChatCompletionCreateParamsNonStreaming,
            requestOptions,
          )) as ChatCompletionLike;
        }
        throw first;
      }
    });

    const choice = response.choices && response.choices.length > 0 ? response.choices[0] : null;
    const msg = choice?.message;
    const result = (msg?.content ?? '').trim();

    const usage = response.usage as Record<string, unknown> | null | undefined;
    if (genObs) {
      try {
        const usageDetails = {
          // Values are forwarded RAW — Python's getattr(usage, key, 0)
          // defaults only when the field is ABSENT and never coerces, so
          // a proxy's string/float/None usage reaches the observation
          // verbatim on both SDKs (unlike the returnUsage path below,
          // which normalises through asInt on both sides).
          input: (usage && 'prompt_tokens' in usage ? usage['prompt_tokens'] : 0) as number,
          output: (usage && 'completion_tokens' in usage
            ? usage['completion_tokens']
            : 0) as number,
        };
        const updatePayload: Record<string, unknown> = {
          output: result,
          usageDetails,
        };
        const costs = costDetails(opts.costFn, opts.model, usageDetails);
        if (costs) {
          updatePayload['costDetails'] = costs;
        }
        genObs.update(updatePayload as Parameters<typeof genObs.update>[0]);
      } catch (err) {
        console.debug('Langfuse generation update failed', err);
      }
    }

    if (opts.returnUsage) {
      // Normalised usage for the caller's accounting (e.g. a slide writer
      // recording per-generation rows). cache_read / cache_write come from
      // the LiteLLM→Anthropic passthrough; 0 when the model/provider
      // doesn't cache.
      const [cacheRead, cacheWrite] = extractCacheTokens(usage);
      return {
        text: result,
        usage: {
          input_tokens: usage ? asInt(usage['prompt_tokens']) : 0,
          output_tokens: usage ? asInt(usage['completion_tokens']) : 0,
          cache_read_tokens: cacheRead,
          cache_write_tokens: cacheWrite,
        },
      };
    }
    return result;
  } catch (err) {
    failure = err;
    throw err;
  } finally {
    // Success, provider errors, retry failures — the observation must close
    // before anything propagates to the caller, errored when the call failed.
    genObs?.end(failure);
  }
}
