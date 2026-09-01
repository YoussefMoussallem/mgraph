/**
 * Provider-agnostic request / message / event schemas.
 *
 * These types form the boundary between application code and the OpenAI SDK.
 * Callers construct a {@link ChatRequest} from their own domain objects, and
 * the adapter translates it into SDK-specific shapes via the mappers. Keeping
 * this translation layer thin lets us swap the underlying client without
 * touching the rest of the codebase. Compile-time interfaces only — no
 * runtime validation; optional-field defaults are applied at read sites.
 */

/**
 * One segment of a structured system prompt.
 *
 * `cache: true` places a prompt-cache breakpoint AFTER this block: the
 * provider caches everything up to and including it, keyed on the exact
 * bytes. Flag the block that ends a stable prefix (static rules, tool docs)
 * and keep volatile content (dates, per-request context) in later, unflagged
 * blocks — any byte churn before a breakpoint turns every call into a cache
 * miss.
 *
 * Adapter methods accept `string | SystemBlock[]` for the system prompt; a
 * plain string is equivalent to one unflagged block and is never cached.
 */
export interface SystemBlock {
  text: string;
  /** Place a cache breakpoint after this block. Default `false`. */
  cache?: boolean;
}

/**
 * The system prompt as plain text — blocks joined with no separator, cache
 * flags ignored.
 *
 * Used wherever `cache_control` can't apply: the Responses path (the
 * `instructions` param), models that cache automatically (OpenAI), and trace
 * payloads.
 */
export function systemText(blocks: SystemBlock[]): string {
  return blocks.map((b) => b.text).join('');
}

/**
 * Inline image payload attached to a user message.
 *
 * Stored as base64 rather than a URL so message history is self-contained
 * and replayable without depending on external storage still being
 * reachable.
 */
export interface ImageData {
  mimeType: string;
  base64: string;
}

/**
 * A single tool/function call produced by the assistant.
 *
 * `arguments` is kept as a raw JSON string rather than a parsed object
 * because the LLM streams it character-by-character; parsing is deferred to
 * the caller so partial arguments can be surfaced to the UI progressively.
 * Never parse or validate it here.
 */
export interface ToolCallData {
  id: string;
  name: string;
  /** Raw JSON string, exactly as streamed by the model. */
  arguments: string;
}

/**
 * One turn of a chat conversation.
 *
 * Shape intentionally matches the three OpenAI roles we support. `content`
 * is optional because assistant turns can consist solely of tool calls, and
 * tool turns carry the call id they're answering in `toolCallId`.
 */
export interface Message {
  role: 'user' | 'assistant' | 'tool';
  content?: string | null;
  images?: ImageData[] | null;
  toolCalls?: ToolCallData[] | null;
  /** For `role: "tool"` — the id of the call this turn answers. */
  toolCallId?: string | null;
}

/**
 * Input envelope for `LLMAdapter.stream` / `complete` / `generate`.
 *
 * - `tools` — optional function/tool definitions. Non-`function` types
 *   (e.g. `web_search_preview`) are passed through to the provider
 *   untouched.
 * - `thinking` — enable reasoning/thinking output. When `true` the adapter
 *   requests reasoning summaries and emits `thinking_delta` events.
 *   Default `false`.
 * - `cacheTtl` — ephemeral cache tier for {@link SystemBlock} breakpoints:
 *   `"5m"` (default; the bare form every backend accepts, incl. Bedrock) or
 *   `"1h"` (Anthropic's extended-cache beta; Bedrock ignores the whole
 *   breakpoint if it sees the explicit ttl field). Only takes effect when
 *   the system prompt contains cache-flagged blocks and the model supports
 *   `cache_control`.
 * - `messageCacheTtl` — when set, the Chat Completions path also caches the
 *   conversation: it attaches a `cache_control` breakpoint at this TTL to
 *   the final message, so the system→history prefix caches and "moves"
 *   forward each turn. Unset (default) caches only the system prefix.
 * - `maxOutputTokens` — cap on tokens the model may generate this turn.
 *   When unset the provider's (often low) default applies — which silently
 *   truncates large tool calls (e.g. writing a big HTML file)
 *   mid-arguments. Callers that may emit large output should set this to
 *   the model's real capacity.
 * - `reasoningEffort` — per-request effort level forwarded when `thinking`
 *   is `true`, overriding the adapter's constructor default. Unset falls
 *   back to the adapter default.
 * - `transport` — explicit wire-path override: `"chat"` forces
 *   chat.completions, `"responses"` forces the Responses API. Unset keeps
 *   the caching-intent routing rule. Forcing `"responses"` on a request
 *   with cache-flagged blocks means the cache flags are NOT realised (the
 *   system prompt rides as plain-text `instructions`); forcing `"chat"`
 *   drops hosted tools (the chat mapper's existing behavior).
 * - `signal` — optional AbortSignal cancelling the underlying HTTP request
 *   (pre-first-byte and mid-stream alike). An abort surfaces as the openai
 *   SDK's `APIUserAbortError`, deliberately NOT translated into the
 *   {@link ProviderError} hierarchy so callers can tell their own abort
 *   apart from provider failures. TS-only divergence: Python cancels via
 *   native asyncio task cancellation instead (see PARITY.md).
 * - `userId` / `sessionId` / `traceMetadata` / `traceTags` — observability
 *   only: Langfuse trace identity (per-user cost aggregation, session
 *   grouping), extra key-value dimensions (small correlating identifiers,
 *   not payloads), and filter tags. NEVER sent to the LLM provider.
 */
export interface ChatRequest {
  /** Provider model id (e.g. `"claude-opus-4-7"`). */
  model: string;
  /** Conversation turns, oldest first. */
  messages: Message[];
  tools?: Record<string, unknown>[] | null;
  thinking?: boolean;
  cacheTtl?: '5m' | '1h';
  messageCacheTtl?: string | null;
  maxOutputTokens?: number | null;
  reasoningEffort?: string | null;
  transport?: 'chat' | 'responses' | null;
  signal?: AbortSignal | null;
  userId?: string | null;
  sessionId?: string | null;
  traceMetadata?: Record<string, unknown> | null;
  traceTags?: string[] | null;
}

/**
 * Normalised streaming event emitted by `LLMAdapter.stream`.
 *
 * Provider-specific SDK events are mapped into a small stable set of names
 * (`text_delta`, `thinking_delta`, `tool_call_start`, `tool_call_delta`,
 * `tool_call_done`, `web_search_*`, `error`, `done`). Event names and every
 * `data` payload key stay snake_case — a wire contract shared byte-for-byte
 * with the Python SDK. Consumers should treat unknown event names as no-ops
 * so new event types can be added without breaking them.
 *
 * The terminal `done` carries `{ usage, stop_reason }`. `stop_reason` is the
 * normalised finish signal: `"end_turn"`, `"max_tokens"` (truncated by the
 * output cap), `"tool_use"` (stopped to call tools), `"error"` (the provider
 * reported a mid-stream failure — an `error` event preceded this `done`), a
 * verbatim provider reason for anything unrecognised (e.g.
 * `"content_filter"`), or `null` when the stream ended without any terminal
 * signal — which consumers should treat as "completion unconfirmed", not as
 * a clean finish.
 *
 * Deliberately validation-free: events are produced in a hot loop where
 * schema validation is measurable overhead.
 */
export class StreamEvent {
  constructor(
    public event: string,
    public data: Record<string, any>,
  ) {}
}
