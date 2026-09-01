/**
 * Exception classification and the adapter's translation boundary.
 * TS twin of Python `tests/test_exceptions.py`.
 *
 * Application code catches the `errors.ts` hierarchy only — the adapter must
 * never leak `openai` SDK error types. The Python originals drive the
 * boundary through `LLMAdapter` with a stubbed `client.responses.create`;
 * here the same invariants (same SDK error types, error surfacing during
 * async iteration for stream vs an awaited call for generate, cause
 * preserved) are pinned on the exported translation helpers the endpoint
 * modules are built from — the full LLMAdapter round-trip with an injected
 * client is exercised by the adapter-level test files.
 */

import { expect, it } from 'vitest';
import { APIConnectionError, APIConnectionTimeoutError, APIError } from 'openai';

import { toProviderError, translateProviderErrors } from '../src/adapter/common.js';
import {
  classifyStatusError,
  ProviderAuthError,
  ProviderConnectionError,
  ProviderError,
  ProviderInvalidRequestError,
  ProviderNotFoundError,
  ProviderRateLimitError,
  ProviderServerError,
} from '../src/errors.js';
import type { StreamEvent } from '../src/schemas.js';

it('classify maps every status family', () => {
  const cases: Array<[number, typeof ProviderError]> = [
    [401, ProviderAuthError],
    [403, ProviderAuthError],
    [429, ProviderRateLimitError],
    [404, ProviderNotFoundError],
    [400, ProviderInvalidRequestError],
    [500, ProviderServerError],
    [503, ProviderServerError],
  ];
  for (const [status, errType] of cases) {
    const err = classifyStatusError(status, 'msg');
    expect(err.constructor, String(status)).toBe(errType);
    expect(err.statusCode).toBe(status);
  }

  // Unmapped codes fall back to the base class, never throw.
  const fallback = classifyStatusError(418, 'teapot');
  expect(fallback.constructor).toBe(ProviderError);
});

function statusError(status: number): APIError {
  return new APIError(status, undefined, 'provider said no', undefined);
}

/** A stubbed SDK call that fails, mirroring the raising `responses.create`. */
function raisingCreate(exc: unknown): () => Promise<never> {
  return () => Promise.reject(exc);
}

/**
 * Minimal endpoint-module stream shape: SDK work wrapped in try/catch that
 * rethrows the translated error, surfacing it to the `for await` consumer.
 */
async function* streamThroughBoundary(create: () => Promise<never>): AsyncGenerator<StreamEvent> {
  try {
    await create();
  } catch (err) {
    throw toProviderError(err) ?? err;
  }
}

async function drain(gen: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of gen) {
    events.push(ev);
  }
  return events;
}

async function rejectionOf(run: Promise<unknown>): Promise<unknown> {
  try {
    await run;
  } catch (err) {
    return err;
  }
  throw new Error('expected rejection, but the call succeeded');
}

it('stream translates status errors', async () => {
  const err = await rejectionOf(drain(streamThroughBoundary(raisingCreate(statusError(429)))));
  expect(err).toBeInstanceOf(ProviderRateLimitError);
  // Original SDK error preserved as the cause for debugging.
  expect((err as ProviderError).cause).toBeInstanceOf(APIError);
});

it('stream translates timeouts to connection error', async () => {
  const err = await rejectionOf(
    drain(streamThroughBoundary(raisingCreate(new APIConnectionTimeoutError()))),
  );
  expect(err).toBeInstanceOf(ProviderConnectionError);
  expect((err as ProviderError).message).toMatch(/timed out/);
});

it('generate translates connection errors', async () => {
  const err = await rejectionOf(translateProviderErrors(raisingCreate(new APIConnectionError({}))));
  expect(err).toBeInstanceOf(ProviderConnectionError);
  // Message parity with Python's `str(e)`: just the SDK's message, WITHOUT
  // the "APIConnectionError: " class-name prefix `String(err)` would add —
  // the hierarchy exists so the openai SDK never leaks, message included.
  expect((err as ProviderError).message).toBe('Connection error.');
});
