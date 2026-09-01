/**
 * @genai-sdk/llm-provider — provider-agnostic LLM client built on the
 * OpenAI Node SDK.
 *
 * Wraps the OpenAI client behind a small normalized schema (Message,
 * ChatRequest, SystemBlock, StreamEvent) so application code never touches
 * SDK-specific types. Main-loop streaming uses the Responses API;
 * cache-flagged requests and utility callers use chat completions.
 *
 * The error hierarchy is exported first-class here (the Python package
 * exposes it via the `llm_provider.exceptions` submodule instead).
 */

export { LLMAdapter } from './adapter/core.js';
export type {
  GenerateChatCompletionOptions,
  GenerateChatCompletionResult,
  LLMAdapterOptions,
} from './adapter/core.js';
export type { ChatCompletionUsage } from './adapter/chatCompletions.js';
export type { GenerateImageOptions, GenerateImageResult } from './adapter/images.js';
export {
  classifyStatusError,
  ProviderAuthError,
  ProviderConnectionError,
  ProviderError,
  ProviderInvalidRequestError,
  ProviderNotFoundError,
  ProviderRateLimitError,
  ProviderServerError,
} from './errors.js';
export type { ProviderErrorOptions } from './errors.js';
export { StreamEvent, systemText } from './schemas.js';
export type { ChatRequest, ImageData, Message, SystemBlock, ToolCallData } from './schemas.js';
