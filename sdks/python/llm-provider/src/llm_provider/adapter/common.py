"""Shared policy and plumbing for the adapter package.

Policy — how a system prompt normalises, which model families take
``cache_control`` — plus the cross-path helpers every endpoint module uses:
usage/cache-token extraction and the OpenAI→provider-agnostic exception
translation wrapped around every SDK call.
"""

import logging
from collections.abc import Callable
from contextlib import contextmanager

import openai

from llm_provider.exceptions import ProviderConnectionError, classify_status_error
from llm_provider.schemas import ChatRequest, SystemBlock

logger = logging.getLogger(__name__)

CostFn = Callable[[str, dict], dict | None]
"""App-supplied pricer: ``(model, usage_details) -> cost_details`` in USD.

The SDK is deliberately pricing-agnostic behind the proxy — it only defines
this interface. The pricer receives the model id and the exact
``usage_details`` dict about to be reported to Langfuse, and returns a cost
dict mirroring those keys (plus optional ``"total"``; Langfuse sums the keys
when it's omitted). Return ``None`` for models it can't price.
"""


def system_blocks(system: str | list[SystemBlock]) -> list[SystemBlock]:
    """Normalise a system prompt to a list of non-empty blocks.

    A plain ``str`` becomes one unflagged block (never cached); empty
    strings and empty-text blocks are dropped. Idempotent, so both the
    facade and the endpoint modules can call it.
    """
    if isinstance(system, str):
        return [SystemBlock(text=system)] if system else []
    return [b for b in system if b.text]


def wants_cache(blocks: list[SystemBlock]) -> bool:
    return any(b.cache for b in blocks)


def trace_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Identity/metadata kwargs for the tracing helpers, unset values omitted.

    The "nothing provided" case stays an empty dict so endpoint modules can
    splat it (``**``) without special-casing.
    """
    return {
        k: v
        for k, v in (
            ("user_id", user_id),
            ("session_id", session_id),
            ("metadata", metadata),
            ("tags", tags),
        )
        if v
    }


def request_trace_attributes(request: ChatRequest) -> dict:
    """Trace attributes carried on the request envelope."""
    return trace_attributes(
        user_id=request.user_id,
        session_id=request.session_id,
        metadata=request.trace_metadata,
        tags=request.trace_tags,
    )


def cost_details(cost_fn: CostFn | None, model: str, usage_details: dict) -> dict | None:
    """``cost_details`` for a Langfuse update, or ``None`` when no pricer is
    configured or it declined/failed.

    Never raises — a buggy app pricer must not take down the traced call
    (same contract as the rest of the tracing plumbing).
    """
    if cost_fn is None:
        return None
    try:
        return cost_fn(model, usage_details) or None
    except Exception:
        logger.warning("cost_fn failed for model %s", model, exc_info=True)
        return None


def supports_cache_control(model: str) -> bool:
    """True for backends where LiteLLM forwards Anthropic-style ``cache_control``
    breakpoints — Anthropic-native, Bedrock Claude, AND Gemini/Vertex (LiteLLM
    maps the breakpoint onto Gemini context caching, so dropping it there
    DISABLES caching).

    OpenAI (gpt / o-series) is the lone exception: it caches **automatically** by
    prefix and rejects ``cache_control`` content blocks, so for it we strip the
    markers and send plain text — its built-in prefix cache does the work, keyed
    on the stable system prefix we place first (``QueryEngine._build_system_prompt``).
    Allowlisting the cache_control families keeps a never-seen model degrading to
    "no breakpoint" (suboptimal) rather than "rejected request" (broken)."""
    m = (model or "").lower()
    return any(fam in m for fam in ("claude", "anthropic", "gemini", "vertex"))


@contextmanager
def translate_provider_errors():
    """Map OpenAI SDK exceptions into the provider-agnostic hierarchy.

    Every endpoint module wraps its SDK calls in this so application code
    never imports ``openai.*`` exceptions. Clause order matters:
    ``APITimeoutError`` subclasses ``APIConnectionError``, so the timeout
    clause must come first to keep its clearer message.
    """
    try:
        yield
    except openai.APIStatusError as e:
        raise classify_status_error(e.status_code, e.message) from e
    except openai.APITimeoutError as e:
        raise ProviderConnectionError("Request timed out") from e
    except openai.APIConnectionError as e:
        raise ProviderConnectionError(str(e)) from e


def as_int(value) -> int:
    """Coerce a possibly-None / non-numeric usage field to int (0 on failure)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _attr_or_extra(obj, name):
    """Read ``name`` off a usage object, falling back to a pydantic model's
    ``model_extra``.

    The OpenAI SDK usage models are typed, but LiteLLM forwards Anthropic's
    cache counters (``cache_read_input_tokens`` / ``cache_creation_input_tokens``)
    as *extra* fields, which land in ``model_extra`` rather than as declared
    attributes. Check both so we capture cache usage regardless of where the
    proxy puts it.
    """
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get(name)
    return value


def extract_cache_tokens(usage) -> tuple[int, int]:
    """Best-effort ``(cache_read, cache_write)`` token counts from a provider
    usage object.

    Spans both call shapes and the LiteLLM→Anthropic passthrough:

    * **reads** — input served from a cache breakpoint. Exposed OpenAI-style as
      ``{input,prompt}_tokens_details.cached_tokens``, or as LiteLLM's
      top-level ``cache_read_input_tokens`` extra.
    * **writes** — input written into the cache this call. Anthropic-only;
      surfaced by LiteLLM as ``cache_creation_input_tokens``.

    Any field that's absent reads as 0, so a non-caching model or provider
    simply reports no cache usage — the "for all models" safe default.
    """
    if usage is None:
        return 0, 0

    read = 0
    for details_attr in ("input_tokens_details", "prompt_tokens_details"):
        details = _attr_or_extra(usage, details_attr)
        if details is None:
            continue
        cached = getattr(details, "cached_tokens", None)
        if cached is None and isinstance(details, dict):
            cached = details.get("cached_tokens")
        read = read or as_int(cached)
    read = read or as_int(_attr_or_extra(usage, "cache_read_input_tokens"))

    write = as_int(_attr_or_extra(usage, "cache_creation_input_tokens"))
    return read, write
