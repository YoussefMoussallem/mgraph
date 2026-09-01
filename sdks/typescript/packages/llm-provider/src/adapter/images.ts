/**
 * The Images API call path (`images.generate`).
 *
 * LiteLLM forwards to the configured image backend (e.g. gpt-image-1 /
 * DALL·E). Bytes are requested inline as `b64_json` so there is no second
 * fetch of a signed URL that could expire before the caller persists them.
 */

import { APIError } from 'openai';
import type OpenAI from 'openai';
import type { ImageGenerateParamsNonStreaming, ImagesResponse } from 'openai/resources/images';

import { generation as langfuseGeneration } from '@genai-sdk/langfuse-client';
import type { TraceAttrs } from '@genai-sdk/langfuse-client';

import { asInt, costDetails, type CostFn, traceAttributes, translateProviderErrors } from './common.js';

/** Options for {@link generateImage} — keyword-only in the Python original. */
export interface GenerateImageOptions {
  model: string;
  prompt: string;
  /** Image dimensions, e.g. `"1024x1024"` (default). */
  size?: string;
  /**
   * Forwarded only when set (non-empty): accepted values differ by model
   * (`standard`/`hd` for DALL·E 3 vs `low`/`medium`/`high` for gpt-image-1)
   * and an unsupported value is a hard 400.
   */
  quality?: string | null;
  /**
   * Forwarded as `background` when set (e.g. `"transparent"` for gpt-image
   * icon cut-outs). Provider-dialect passthrough like `quality` — an
   * unsupported value is the provider's 400 to raise, not ours to guess.
   */
  background?: string | null;
  /** Forwarded as `output_format` when set (e.g. `"png"` with a transparent background). */
  outputFormat?: string | null;
  /**
   * Number of images to generate. Default 1. Pass `null` to OMIT the
   * parameter entirely — some OpenAI-compatible image backends reject `n`
   * outright (same opt-out contract as `temperature: null` on the chat
   * utility path).
   */
  n?: number | null;
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
  /** Optional Langfuse cost pricer — see {@link CostFn}. */
  costFn?: CostFn | null;
}

/** Result of {@link generateImage} — Python's `(images, usage)` tuple plus URL-dialect items. */
export interface GenerateImageResult {
  /** Decoded PNG/JPEG bytes, in provider order; undecodable items skipped. */
  images: Buffer[];
  /**
   * Result URLs for items the provider returned WITHOUT inline bytes (some
   * OpenAI-compatible backends — e.g. CogView behind a proxy — answer with a
   * `url` even when `b64_json` was requested). The adapter never fetches
   * them (no network side-trips beyond the provider call; URLs may need the
   * caller's egress policy) — callers that support the URL dialect download
   * these promptly, before they expire. Empty for b64-native providers.
   */
  urls: string[];
  /**
   * Exactly `{ input_tokens, output_tokens }` (snake_case wire keys).
   * Token-billed models (gpt-image-1) report counts that scale with the
   * requested size/quality so the caller can price the render per-token;
   * flat-priced models (DALL·E) report no usage and the counts are 0.
   */
  usage: Record<string, number>;
}

/**
 * Decode a base64 payload with Python `base64.b64decode` default (lenient)
 * semantics, or return `null` where Python would raise `binascii.Error`.
 *
 * Python's default decode (validate=False) DISCARDS every character outside
 * the standard alphabet before decoding — whitespace, control bytes, even a
 * URL-safe-alphabet `-`/`_` from a nonconforming backend — and only rejects
 * the payload afterwards when the surviving data has bad padding/length. So
 * stray non-alphabet characters must not lose the image; only genuinely
 * malformed payloads are dropped. (`Buffer.from(s, "base64")` alone never
 * signals failure, hence the explicit shape check after the discard.)
 */
function decodeBase64Lenient(b64: string): Buffer | null {
  const compact = b64.replace(/[^A-Za-z0-9+/=]/g, '');
  if (compact.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(compact)) {
    return null;
  }
  return Buffer.from(compact, 'base64');
}

/**
 * Generate `n` image(s) from a text `prompt` — returns raw bytes.
 *
 * `response_format: "b64_json"` is always sent on the first attempt: DALL·E
 * needs it to avoid URL-only output that can expire before the caller
 * persists the bytes, but gpt-image-1 rejects the param (it always returns
 * b64_json). When the provider flags it as unknown (400 mentioning
 * `response_format`), retry exactly once without it so a single configured
 * model id works for either image family; a proxy that normalises the param
 * away simply succeeds on the first call.
 *
 * Undecodable base64 items are logged and skipped, never thrown. SDK errors
 * surface as the provider-agnostic error hierarchy, and the Langfuse
 * observation is closed on every path — tracing must never leak on failures.
 */
export async function generateImage(
  client: OpenAI,
  options: GenerateImageOptions,
): Promise<GenerateImageResult> {
  const {
    model,
    prompt,
    size = '1024x1024',
    quality,
    background,
    outputFormat,
    n = 1,
    userId,
    sessionId,
    traceMetadata,
    traceTags,
    costFn,
  } = options;

  const params: Record<string, unknown> = {
    model,
    prompt,
    size,
    response_format: 'b64_json',
  };
  if (n !== null) {
    params['n'] = n;
  }
  if (quality) {
    params['quality'] = quality;
  }
  if (background) {
    params['background'] = background;
  }
  if (outputFormat) {
    params['output_format'] = outputFormat;
  }

  // Don't log the (potentially large) base64 output as input; the prompt +
  // params are the useful trace fields. Snapshot before any retry mutation.
  const { response_format: _responseFormat, ...traceInput } = params;
  const genObs = langfuseGeneration(
    'llm-image-generate',
    model,
    traceInput,
    traceAttributes({
      userId,
      sessionId,
      metadata: traceMetadata,
      tags: traceTags,
    }) as TraceAttrs,
  );

  const callImages = (): Promise<ImagesResponse> =>
    client.images.generate(
      params as unknown as ImageGenerateParamsNonStreaming,
      options.signal ? { signal: options.signal } : undefined,
    );

  let response: ImagesResponse;
  try {
    response = await translateProviderErrors(async () => {
      try {
        return await callImages();
      } catch (first) {
        // The retry intercepts the RAW SDK error before translation.
        if (
          first instanceof APIError &&
          first.status === 400 &&
          String(first.message ?? '')
            .toLowerCase()
            .includes('response_format')
        ) {
          delete params['response_format'];
          return await callImages();
        }
        throw first;
      }
    });
  } catch (err) {
    // Close the observation WITH the propagating error — tracing must never
    // leak on failures, and the failure must be recorded on the span
    // (Python: `gen_ctx.__exit__(type(e), e, e.__traceback__)`).
    genObs?.end(err);
    throw err;
  }

  const data = response.data ?? [];
  const images: Buffer[] = [];
  const urls: string[] = [];
  for (const item of data) {
    const b64 = item?.b64_json;
    if (!b64) {
      // URL dialect: surface instead of silently dropping the render — the
      // caller downloads (the adapter takes no network side-trips).
      const url = (item as { url?: unknown } | null | undefined)?.url;
      if (typeof url === 'string' && url) {
        urls.push(url);
      }
      continue;
    }
    const decoded = decodeBase64Lenient(b64);
    if (decoded === null) {
      console.warn('Image API returned undecodable b64 payload');
      continue;
    }
    images.push(decoded);
  }

  // Token usage: token-billed image models (gpt-image-1) return counts that
  // scale with size/quality; flat-priced models (DALL·E) report no usage —
  // zeros here, and the caller falls back to the per-image rate.
  const usage = response.usage as { input_tokens?: unknown; output_tokens?: unknown } | undefined;
  const usageOut = {
    input_tokens: usage ? asInt(usage.input_tokens) : 0,
    output_tokens: usage ? asInt(usage.output_tokens) : 0,
  };

  if (genObs) {
    try {
      const usageDetails = {
        input: usageOut.input_tokens,
        output: usageOut.output_tokens,
        // Custom usage type: image count, so flat-priced models (DALL·E —
        // zero token usage) can still be priced per image by the app's
        // `costFn`.
        images: images.length,
      };
      const updatePayload: Record<string, unknown> = {
        output: { image_count: images.length },
        usageDetails,
      };
      const costs = costDetails(costFn, model, usageDetails);
      if (costs) {
        updatePayload['costDetails'] = costs;
      }
      genObs.update(updatePayload as Parameters<typeof genObs.update>[0]);
    } catch (err) {
      console.debug('Langfuse generation update failed', err);
    }
  }
  genObs?.end();

  return { images, urls, usage: usageOut };
}
