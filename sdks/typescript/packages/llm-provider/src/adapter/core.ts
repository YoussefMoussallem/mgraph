/**
 * The {@link LLMAdapter} facade — the single entry point for provider calls.
 *
 * Exposes call shapes built on the OpenAI Node SDK, delegating per-endpoint
 * work to the sibling modules (mirroring `mappers/`):
 *
 * - `stream()` — low-level async iterator of normalised {@link StreamEvent}s;
 *   routes to the responses or chat-completions path depending on caching
 *   intent.
 * - `complete()` — wraps `stream()` and collapses text deltas into a single
 *   `text` event while forwarding status/tool events live.
 * - `generate()` — non-streaming single-shot call via the responses path.
 * - `generateChatCompletion()` — non-streaming chat-completions call for
 *   simple text in/out (e.g. when the Responses API is unavailable on a
 *   given Azure region).
 * - `generateImage()` — image generation via the images module.
 *
 * Every call is wrapped in a Langfuse observation when tracing is
 * configured, and SDK exceptions are translated into the provider-agnostic
 * error hierarchy at the boundary.
 */

import OpenAI from 'openai';

import { span as langfuseSpan } from '@genai-sdk/langfuse-client';
import type { TraceAttrs } from '@genai-sdk/langfuse-client';

import { buildInput, buildTools } from '../mappers/responses.js';
import { StreamEvent, systemText } from '../schemas.js';
import type { ChatRequest, SystemBlock } from '../schemas.js';
import * as chatCompletions from './chatCompletions.js';
import type { ChatCompletionUsage } from './chatCompletions.js';
import {
  asInt,
  type CostFn,
  requestTraceAttributes,
  systemBlocks,
  wantsCache,
} from './common.js';
import * as images from './images.js';
import type { GenerateImageOptions, GenerateImageResult } from './images.js';
import * as responses from './responses.js';

/** Constructor options for {@link LLMAdapter}. */
export interface LLMAdapterOptions {
  /** Credential passed straight to the SDK. */
  apiKey: string;
  /**
   * Provider endpoint. Can point at a proxy (e.g. an internal gateway) to
   * rewrite model names or add auth.
   */
  baseUrl: string;
  /**
   * Per-request timeout in seconds (default 600). Streaming calls keep the
   * socket open the whole time, so this needs to cover the longest plausible
   * response, not just the TTFT. Converted to the SDK's millisecond timeout
   * internally.
   */
  timeoutSeconds?: number;
  /**
   * Default effort level forwarded when `ChatRequest.thinking` is `true`.
   * Tuned per deployment. Default `"medium"`.
   */
  reasoningEffort?: string;
  /**
   * Injected OpenAI client — the test seam replacing Python's monkeypatched
   * `adapter.client`. When provided, no client is constructed and
   * `apiKey`/`baseUrl`/`timeoutSeconds` are not used.
   */
  client?: OpenAI;
  /**
   * Optional app-supplied pricer that attaches USD costs to every Langfuse
   * observation — see {@link CostFn}. The SDK holds no pricing table itself
   * (model names behind the proxy are deployment-specific), so cost tracking
   * only happens when the app injects this. Exceptions are caught and logged;
   * pricing can never break a call.
   */
  costFn?: CostFn | null;
}

/**
 * Options for {@link LLMAdapter.generateChatCompletion} — keyword-only in
 * the Python original.
 *
 * `userContent` accepts a plain string (the original utility shape — title /
 * label generation) or a list of OpenAI content parts
 * (`{"type": "text", ...}`, `{"type": "image_url", ...}`) for vision calls;
 * either form is forwarded verbatim.
 */
export interface GenerateChatCompletionOptions {
  model: string;
  /**
   * Plain text or {@link SystemBlock}s; cache-flagged blocks become
   * `cache_control` ephemeral breakpoints on supporting models (a caller may
   * flag several). Plain text is never cached.
   */
  systemPrompt: string | SystemBlock[];
  userContent: string | Array<Record<string, unknown>>;
  /**
   * Sampling temperature (default 0.3). Dropped — and the model memoised —
   * when the model hard-400s on it. Pass `null` to omit the parameter
   * entirely (the provider's own default applies).
   */
  temperature?: number | null;
  /**
   * Cap on tokens the model may generate (`max_tokens`). Unset → the
   * provider's (often low) default. Same contract as
   * `ChatRequest.maxOutputTokens`.
   */
  maxOutputTokens?: number | null;
  /**
   * Cache tier for cache-flagged system blocks. Default `"1h"` (unlike the
   * streaming path's `"5m"` request default).
   */
  cacheTtl?: string;
  /** Resolve to `{ text, usage }` instead of the bare string. Default false. */
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

/** `returnUsage: true` result — Python's `(text, usage)` tuple. */
export interface GenerateChatCompletionResult {
  /** Assistant text; `""` when the model returned no text (caller-treated failure). */
  text: string;
  /** Snake_case wire keys (`input_tokens`, `cache_read_tokens`, ...). */
  usage: ChatCompletionUsage;
}

/**
 * Async, provider-agnostic LLM client.
 *
 * One adapter per (apiKey, baseUrl) pair; typically instantiated once at
 * startup and injected wherever LLM access is needed. Safe to share across
 * concurrent callers — the underlying OpenAI client handles concurrency.
 */
export class LLMAdapter {
  /** The SDK client; endpoint modules receive it as their first argument. */
  readonly client: OpenAI;
  /** Default reasoning effort forwarded when `ChatRequest.thinking` is true. */
  readonly reasoningEffort: string;
  /**
   * Models that rejected the `temperature` parameter, learned at runtime
   * from 400 responses so subsequent calls skip the doomed first attempt.
   * Adapter-lifetime — and the adapter is a startup singleton, so
   * effectively process-lifetime. Passed BY REFERENCE to the
   * chat-completions module, which mutates it.
   */
  private readonly temperatureUnsupported = new Set<string>();
  /** Optional Langfuse cost pricer — forwarded to every traced call shape. */
  readonly costFn: CostFn | null;

  constructor(options: LLMAdapterOptions) {
    this.client =
      options.client ??
      new OpenAI({
        apiKey: options.apiKey,
        baseURL: options.baseUrl,
        timeout: (options.timeoutSeconds ?? 600) * 1000,
      });
    this.reasoningEffort = options.reasoningEffort ?? 'medium';
    this.costFn = options.costFn ?? null;
  }

  /**
   * Stream a model response as normalised {@link StreamEvent}s
   * (`text_delta`, `thinking_delta`, `tool_call_*`, `web_search_*`, `error`,
   * `done` — the `done` carrying `{ usage, stop_reason }`).
   *
   * Routing: an explicit `request.transport` (`"chat"` | `"responses"`)
   * wins outright. Otherwise requests with caching intent — cache-flagged
   * {@link SystemBlock}s or a `messageCacheTtl` — stream over
   * chat.completions, where LiteLLM honors content-block `cache_control` so
   * the prefix actually caches on Bedrock; the responses path serves the
   * rest (hosted web search, utility streaming). Forcing `"responses"` on a
   * cache-flagged request means the flags are not realised; forcing
   * `"chat"` drops hosted tools (the chat mapper's existing behavior).
   *
   * A plain (non-generator) method: routing runs at call time, and the
   * chosen path's async generator is returned directly, so abandoning the
   * stream closes the real generator — and its Langfuse observation —
   * deterministically.
   */
  stream(request: ChatRequest, systemPrompt: string | SystemBlock[]): AsyncGenerator<StreamEvent> {
    const blocks = systemBlocks(systemPrompt);
    const useChat =
      request.transport != null
        ? request.transport === 'chat'
        : wantsCache(blocks) || Boolean(request.messageCacheTtl);
    if (useChat) {
      return chatCompletions.stream(this.client, request, blocks, {
        reasoningEffort: this.reasoningEffort,
        costFn: this.costFn,
      });
    }
    return responses.stream(this.client, request, blocks, {
      reasoningEffort: this.reasoningEffort,
      costFn: this.costFn,
    });
  }

  /**
   * Buffer text deltas, forward everything else live.
   *
   * Intended for callers that want tool/search status updates in real time
   * but don't care about streaming text character-by-character — e.g.
   * background jobs or tests. Yields a single `text` event with the
   * concatenated output just before the final `done`, whose `usage` is the
   * full dict from the inner stream so cache_read / cache_write counters
   * survive to the caller.
   */
  async *complete(
    request: ChatRequest,
    systemPrompt: string | SystemBlock[],
  ): AsyncGenerator<StreamEvent> {
    const textParts: string[] = [];
    let inputTokens = 0;
    let outputTokens = 0;
    let usageData: Record<string, unknown> = { input_tokens: 0, output_tokens: 0 };
    let stopReason: unknown = null;

    const spanObs = langfuseSpan(
      'llm-complete',
      request.model,
      {
        system: systemText(systemBlocks(systemPrompt)),
        messages: buildInput(request.messages),
        tools: buildTools(request.tools),
      },
      requestTraceAttributes(request) as TraceAttrs,
    );

    // The exception (if any) propagating out of the try below, captured so
    // the span closes errored — the counterpart of Python's
    // `span_ctx.__exit__(*sys.exc_info())`. Consumer abandonment (early
    // `return()`) runs only the finally, leaving this unset: abandoned
    // spans close cleanly on both sides.
    let failure: unknown;

    try {
      for await (const event of this.stream(request, systemPrompt)) {
        if (event.event === 'text_delta') {
          const text = event.data['text'];
          if (typeof text !== 'string') {
            // Python fails loudly here (KeyError on a missing key; a None
            // value crashes the final join). A malformed text_delta must
            // never silently corrupt the concatenated output.
            throw new TypeError(
              `text_delta event carried no text payload (got ${text === null ? 'null' : typeof text})`,
            );
          }
          textParts.push(text);
        } else if (event.event === 'done') {
          // The inner `done` is swallowed; a missing or falsy usage (Python
          // truthiness — an empty object counts as absent) keeps the
          // previous dict.
          const usage = event.data['usage'] as Record<string, unknown> | null | undefined;
          if (usage && Object.keys(usage).length > 0) {
            usageData = usage;
          }
          // stop_reason rides the inner done too — forwarded on the outer
          // envelope so buffered callers keep the finish signal.
          stopReason = event.data['stop_reason'] ?? null;
          inputTokens = asInt(usageData['input_tokens']);
          outputTokens = asInt(usageData['output_tokens']);
        } else {
          yield event;
        }
      }

      if (textParts.length > 0) {
        yield new StreamEvent('text', { text: textParts.join('') });
      }

      if (spanObs) {
        try {
          spanObs.update({
            output: textParts.join(''),
            metadata: { input_tokens: inputTokens, output_tokens: outputTokens },
          });
        } catch (err) {
          console.debug('Langfuse complete span update failed', err);
        }
      }
    } catch (err) {
      failure = err;
      throw err;
    } finally {
      // Runs on completion, inner-stream errors, AND consumer abandonment
      // (the consumer's `break` calls return()) — the span must never leak.
      // A propagating error is recorded on the span (Python forwards
      // `sys.exc_info()` into the Langfuse context-manager exit).
      spanObs?.end(failure);
    }

    // Outside the try/finally: emitted only on full success, after the span
    // closes. Forwards the full usage dict from the inner stream (same
    // object) so cache counters survive to the caller, plus its
    // stop_reason so truncation stays visible through the buffering.
    yield new StreamEvent('done', { usage: usageData, stop_reason: stopReason });
  }

  /**
   * Single non-streaming call — returns the assembled text response.
   *
   * Use this when the caller just wants the final answer and doesn't care
   * about intermediate events (prompt refinement, summarisation, anywhere
   * streaming would add complexity without user value). Always the
   * responses path — cache flags and `messageCacheTtl` do NOT reroute it.
   *
   * Only the `output_text` parts of `message` items are concatenated; tool
   * calls, reasoning, and other output types are dropped. Callers that need
   * those should use {@link stream} instead.
   */
  async generate(request: ChatRequest, systemPrompt: string | SystemBlock[] = ''): Promise<string> {
    return responses.generate(this.client, request, systemPrompt, {
      reasoningEffort: this.reasoningEffort,
      costFn: this.costFn,
    });
  }

  /**
   * Single non-streaming **Chat Completions** call — plain text in/out.
   *
   * Use for short utility generations (e.g. chat titles, labels) where the
   * Responses API may be unavailable — Azure OpenAI often exposes
   * chat.completions in regions that do not yet enable responses. Same
   * credentials and base URL as the rest of the adapter; LiteLLM forwards
   * to the appropriate backend. Does **not** support tools or reasoning —
   * only system + user messages.
   *
   * Resolves to the assistant's text (empty string when the model returned
   * no text — treat as failure), or `{ text, usage }` when
   * `returnUsage: true`.
   */
  generateChatCompletion(
    options: GenerateChatCompletionOptions & { returnUsage: true },
  ): Promise<GenerateChatCompletionResult>;
  generateChatCompletion(
    options: GenerateChatCompletionOptions & { returnUsage?: false },
  ): Promise<string>;
  async generateChatCompletion(
    options: GenerateChatCompletionOptions,
  ): Promise<string | GenerateChatCompletionResult> {
    const opts = {
      ...options,
      temperatureUnsupported: this.temperatureUnsupported,
      costFn: this.costFn,
    };
    if (options.returnUsage) {
      return chatCompletions.generateChatCompletion(this.client, { ...opts, returnUsage: true });
    }
    return chatCompletions.generateChatCompletion(this.client, { ...opts, returnUsage: false });
  }

  /**
   * Generate `n` image(s) from a text prompt — returns raw bytes.
   *
   * Uses the **Images** API, which LiteLLM forwards to the configured image
   * backend (e.g. gpt-image-1 / DALL·E); bytes come back inline as
   * `b64_json`. See {@link images.generateImage} for the parameter
   * negotiation and usage semantics. Rejects with the provider-agnostic
   * error types on failure.
   */
  async generateImage(options: GenerateImageOptions): Promise<GenerateImageResult> {
    return images.generateImage(this.client, { ...options, costFn: this.costFn });
  }

  /**
   * Fetch available models from the configured endpoint.
   *
   * Intended for admin UIs and health checks; returns only the fields we
   * actually use (`id`, `ownedBy`) rather than the SDK's full model record,
   * which is noisy and version-dependent.
   *
   * Parity quirk preserved from the Python SDK: this is the ONE adapter
   * method with no error translation — raw SDK exceptions propagate.
   */
  async listModels(): Promise<Array<{ id: string; ownedBy: string }>> {
    const models = await this.client.models.list();
    return models.data.map((m) => ({ id: m.id, ownedBy: m.owned_by }));
  }
}
