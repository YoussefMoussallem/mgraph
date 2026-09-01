"""The Images API call path (``images.generate``).

LiteLLM forwards to the configured image backend (e.g. ``gpt-image-1`` /
DALL·E). Bytes come back inline as ``b64_json`` so there is no second fetch
of a signed URL that could expire before the caller persists them.
"""

import base64
import logging

import openai
from langfuse_client import generation as langfuse_generation

from llm_provider.adapter.common import (
    CostFn,
    cost_details,
    trace_attributes,
    translate_provider_errors,
)

logger = logging.getLogger(__name__)


async def generate_image(
    client: openai.AsyncOpenAI,
    *,
    model: str,
    prompt: str,
    size: str = "1024x1024",
    quality: str | None = None,
    n: int = 1,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_metadata: dict | None = None,
    trace_tags: list[str] | None = None,
    cost_fn: CostFn | None = None,
) -> tuple[list[bytes], dict[str, int]]:
    """Generate ``n`` image(s) from a text ``prompt`` — returns raw bytes.

    ``quality`` is forwarded only when set, because the accepted values
    differ by model (e.g. ``standard``/``hd`` for DALL·E 3 vs
    ``low``/``medium``/``high`` for gpt-image-1) and an unsupported value is
    a hard 400.

    Returns ``(images, usage)`` where ``images`` is a list of ``len == n``
    PNG/JPEG byte strings and ``usage`` is
    ``{"input_tokens": int, "output_tokens": int}``. Token-billed models
    (e.g. gpt-image-1) populate ``usage`` with counts that scale with the
    requested size/quality, so the caller can price the render from the
    proxy's per-token rates exactly like a text turn; flat-priced models
    (DALL·E) report no usage and the counts are ``0``.
    """
    kwargs: dict = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": n,
        "response_format": "b64_json",
    }
    if quality:
        kwargs["quality"] = quality

    gen_ctx = langfuse_generation(
        "llm-image-generate",
        model,
        # Don't log the (potentially large) base64 output as input; the
        # prompt + params are the useful trace fields.
        input_data={k: v for k, v in kwargs.items() if k != "response_format"},
        **trace_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=trace_metadata,
            tags=trace_tags,
        ),
    )
    gen_obs = gen_ctx.__enter__() if gen_ctx else None

    try:
        with translate_provider_errors():
            try:
                response = await client.images.generate(**kwargs)
            except openai.APIStatusError as first:
                # gpt-image-1 rejects ``response_format`` (it always returns
                # b64_json); DALL·E needs it to avoid URL-only output that can
                # expire before the caller persists the bytes. When the
                # provider flags the param as unknown, retry once without it so
                # a single configured model id works for either image family.
                # No-op when the proxy already normalises the param away (the
                # first call simply succeeds).
                if first.status_code == 400 and "response_format" in (
                    (first.message or "").lower()
                ):
                    kwargs.pop("response_format", None)
                    response = await client.images.generate(**kwargs)
                else:
                    raise
    except BaseException as e:
        # Close the observation with the propagating exception — tracing
        # must never leak on failures.
        if gen_ctx:
            gen_ctx.__exit__(type(e), e, e.__traceback__)
        raise

    data = getattr(response, "data", None) or []
    images: list[bytes] = []
    for item in data:
        b64 = getattr(item, "b64_json", None)
        if not b64:
            continue
        try:
            images.append(base64.b64decode(b64))
        except (ValueError, TypeError):
            logger.warning("Image API returned undecodable b64 payload", exc_info=True)

    # Token usage. gpt-image-1 (and other token-billed image models)
    # return a ``usage`` whose output-token count scales with the
    # requested size/quality; the caller prices the render from the
    # proxy's per-token rates. Flat-priced models (DALL·E) report no
    # usage — zeros here, and the caller falls back to the per-image rate.
    usage = getattr(response, "usage", None)
    usage_out = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
    }

    if gen_obs:
        try:
            usage_details = {
                "input": usage_out["input_tokens"],
                "output": usage_out["output_tokens"],
                # Custom usage type: image count, so flat-priced models
                # (DALL·E — zero token usage) can still be priced per image
                # by the app's ``cost_fn``.
                "images": len(images),
            }
            update_kwargs: dict = {
                "output": {"image_count": len(images)},
                "usage_details": usage_details,
            }
            if costs := cost_details(cost_fn, model, usage_details):
                update_kwargs["cost_details"] = costs
            gen_obs.update(**update_kwargs)
        except Exception:
            logger.debug("Langfuse generation update failed", exc_info=True)
    if gen_ctx:
        gen_ctx.__exit__(None, None, None)

    return images, usage_out
