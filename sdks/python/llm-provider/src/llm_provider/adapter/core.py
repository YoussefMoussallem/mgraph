"""The :class:`LLMAdapter` facade — the single entry point for provider calls.

Exposes call shapes built on top of the OpenAI Python SDK, delegating the
per-endpoint work to the sibling modules (mirroring
:mod:`llm_provider.mappers`):

- :meth:`LLMAdapter.stream` — low-level async iterator of normalised
  :class:`StreamEvent`\\ s; routes to :mod:`.responses` or
  :mod:`.chat_completions` depending on caching intent.
- :meth:`LLMAdapter.complete` — wraps :meth:`stream` and collapses text
  deltas into a single ``text`` event while forwarding status/tool events
  live.
- :meth:`LLMAdapter.generate` — non-streaming single-shot call via
  :mod:`.responses`.
- :meth:`LLMAdapter.generate_chat_completion` — non-streaming call via
  :mod:`.chat_completions` for simple text in/out (e.g. when the Responses
  API is unavailable on a given Azure region).
- :meth:`LLMAdapter.generate_image` — image generation via :mod:`.images`.

Every call is wrapped in a Langfuse observation when tracing is configured,
and SDK exceptions are translated into the provider-agnostic
:mod:`~llm_provider.exceptions` hierarchy at the boundary
(:func:`~llm_provider.adapter.common.translate_provider_errors`).
"""

import logging
import sys
from collections.abc import AsyncIterator

import openai
from langfuse_client import span as langfuse_span

from llm_provider.adapter import chat_completions, images, responses
from llm_provider.adapter.common import (
    CostFn,
    as_int,
    request_trace_attributes,
    system_blocks,
    wants_cache,
)
from llm_provider.mappers.responses import build_input, build_tools
from llm_provider.schemas import ChatRequest, StreamEvent, SystemBlock, system_text

logger = logging.getLogger(__name__)


class LLMAdapter:
    """Async, provider-agnostic LLM client.

    One adapter per (api_key, base_url) pair; typically instantiated once at
    startup and injected wherever LLM access is needed. Safe to share across
    coroutines — the underlying ``AsyncOpenAI`` client handles concurrency.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: int = 600,
        reasoning_effort: str = "medium",
        cost_fn: CostFn | None = None,
    ):
        """Build the adapter.

        Args:
            api_key: Credential passed straight to the SDK.
            base_url: Provider endpoint. Can point at a proxy (e.g. an
                internal gateway) to rewrite model names or add auth.
            timeout: Per-request timeout in seconds. Streaming calls keep
                the socket open the whole time, so this needs to cover the
                longest plausible response, not just the TTFT.
            reasoning_effort: Default effort level forwarded when
                ``ChatRequest.thinking`` is ``True``. Tuned per deployment.
            cost_fn: Optional app-supplied pricer
                ``(model, usage_details) -> cost_details`` that attaches USD
                costs to every Langfuse observation — see
                :data:`~llm_provider.adapter.common.CostFn`. The SDK holds no
                pricing table itself (model names behind the proxy are
                deployment-specific), so cost tracking only happens when the
                app injects this. Exceptions are caught and logged; pricing
                can never break a call.
        """
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.reasoning_effort = reasoning_effort
        self.cost_fn = cost_fn
        # Models that rejected the ``temperature`` parameter, learned at
        # runtime from 400 responses so subsequent calls skip the doomed
        # first attempt. Adapter-lifetime — and the adapter is a startup
        # singleton, so effectively process-lifetime.
        self._temperature_unsupported: set[str] = set()

    def stream(
        self, request: ChatRequest, system_prompt: str | list[SystemBlock]
    ) -> AsyncIterator[StreamEvent]:
        """Stream a model response as normalised :class:`StreamEvent`\\ s
        (``text_delta``, ``thinking_delta``, ``tool_call_*``,
        ``web_search_*``, ``error``, ``done``).

        Routing: requests with caching intent — cache-flagged
        :class:`SystemBlock`\\ s or a ``message_cache_ttl`` — stream over
        ``chat.completions``, where LiteLLM honors content-block
        ``cache_control`` so the prefix actually caches on Bedrock; the
        Responses path serves the rest (hosted web search, utility
        streaming).

        Returns the chosen path's async generator directly, so abandoning
        the stream closes the real generator — and its Langfuse
        observation — deterministically.
        """
        blocks = system_blocks(system_prompt)
        if wants_cache(blocks) or request.message_cache_ttl:
            return chat_completions.stream(
                self.client,
                request,
                blocks,
                reasoning_effort=self.reasoning_effort,
                cost_fn=self.cost_fn,
            )
        return responses.stream(
            self.client,
            request,
            blocks,
            reasoning_effort=self.reasoning_effort,
            cost_fn=self.cost_fn,
        )

    

    async def complete(
        self, request: ChatRequest, system_prompt: str | list[SystemBlock]
    ) -> AsyncIterator[StreamEvent]:
        """Buffer text deltas, forward everything else live.

        Intended for callers that want tool/search status updates in real
        time but don't care about streaming text character-by-character — e.g.
        background jobs or tests. Yields a single ``text`` event with the
        concatenated output just before the final ``done``.
        """
        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        usage_data: dict = {"input_tokens": 0, "output_tokens": 0}

        span_ctx = langfuse_span(
            "llm-complete",
            request.model,
            input_data={
                "system": system_text(system_blocks(system_prompt)),
                "messages": build_input(request.messages),
                "tools": build_tools(request.tools),
            },
            **request_trace_attributes(request),
        )
        span_obs = span_ctx.__enter__() if span_ctx else None

        try:
            async for event in self.stream(request, system_prompt):
                if event.event == "text_delta":
                    text_parts.append(event.data["text"])
                elif event.event == "done":
                    usage_data = event.data.get("usage", usage_data) or usage_data
                    input_tokens = as_int(usage_data.get("input_tokens"))
                    output_tokens = as_int(usage_data.get("output_tokens"))
                else:
                    yield event

            if text_parts:
                yield StreamEvent("text", {"text": "".join(text_parts)})

            if span_obs:
                try:
                    span_obs.update(
                        output="".join(text_parts),
                        metadata={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    )
                except Exception:
                    logger.debug("Langfuse complete span update failed", exc_info=True)
        finally:
            # Runs on completion, inner-stream errors, AND consumer
            # abandonment (GeneratorExit) — the span must never leak.
            if span_ctx:
                span_ctx.__exit__(*sys.exc_info())

        # Forward the full usage dict from the inner stream so cache_read /
        # cache_write tokens survive to the caller.
        yield StreamEvent("done", {"usage": usage_data})

    async def generate(
        self, request: ChatRequest, system_prompt: str | list[SystemBlock] = ""
    ) -> str:
        """Single non-streaming call — returns the assembled text response.

        Use this when the caller just wants the final answer and doesn't
        care about intermediate events (prompt refinement, summarisation,
        anywhere streaming would add complexity without user value).

        Only the ``output_text`` parts of ``message`` items are concatenated;
        tool calls, reasoning, and other output types are dropped. Callers
        that need those should use :meth:`stream` instead.
        """
        return await responses.generate(
            self.client,
            request,
            system_prompt,
            reasoning_effort=self.reasoning_effort,
            cost_fn=self.cost_fn,
        )

    async def generate_chat_completion(
        self,
        *,
        model: str,
        system_prompt: str | list[SystemBlock],
        user_content: str | list[dict],
        temperature: float = 0.3,
        cache_ttl: str = "1h",
        return_usage: bool = False,
        user_id: str | None = None,
        session_id: str | None = None,
        trace_metadata: dict | None = None,
        trace_tags: list[str] | None = None,
    ) -> str | tuple[str, dict]:
        """Single non-streaming **Chat Completions** call — plain text in/out.

        Use for short utility generations (e.g. chat titles, labels) where
        the Responses API may be unavailable — Azure OpenAI often exposes
        ``chat.completions`` in regions that do not yet enable
        ``responses.create``. Same credentials and ``base_url`` as the rest
        of the adapter; LiteLLM forwards to the appropriate backend.

        ``user_content`` accepts either a plain string (the original utility
        shape — title / label generation) **or** a list of OpenAI content
        parts (``{"type": "text", "text": ...}``, ``{"type": "image_url",
        "image_url": {"url": ...}}``) for vision calls (e.g. describing an
        uploaded image). The OpenAI Chat Completions API accepts both forms
        natively, so we forward whatever the caller passed.

        Returns the assistant's ``content`` string, or empty string if the
        model returned no text (caller should treat as failure).

        Does **not** support tools or reasoning — only system + user messages.

        ``system_prompt`` accepts plain text or :class:`SystemBlock`\\ s;
        cache-flagged blocks become ``cache_control`` ephemeral breakpoints
        (a caller may flag several — e.g. one after static rules and one
        after a template appendix). Plain text is never cached.
        """
        return await chat_completions.generate_chat_completion(
            self.client,
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=temperature,
            temperature_unsupported=self._temperature_unsupported,
            cache_ttl=cache_ttl,
            return_usage=return_usage,
            user_id=user_id,
            session_id=session_id,
            trace_metadata=trace_metadata,
            trace_tags=trace_tags,
            cost_fn=self.cost_fn,
        )

    async def generate_image(
        self,
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
    ) -> tuple[list[bytes], dict[str, int]]:
        """Generate ``n`` image(s) from a text ``prompt`` — returns raw bytes.

        Uses the **Images** API which LiteLLM forwards to the configured
        image backend (e.g. ``gpt-image-1`` / DALL·E); bytes come back
        inline as ``b64_json``. Returns ``(images, usage)``; see
        :func:`llm_provider.adapter.images.generate_image` for the parameter
        negotiation and usage semantics. Raises the provider-agnostic
        :mod:`~llm_provider.exceptions` types on failure.
        """
        return await images.generate_image(
            self.client,
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            n=n,
            user_id=user_id,
            session_id=session_id,
            trace_metadata=trace_metadata,
            trace_tags=trace_tags,
            cost_fn=self.cost_fn,
        )

    async def list_models(self) -> list[dict]:
        """Fetch available models from the configured endpoint.

        Intended for admin UIs and health checks; returns only the fields we
        actually use (``id``, ``owned_by``) rather than the SDK's full model
        record, which is noisy and version-dependent.
        """
        models = await self.client.models.list()
        return [{"id": m.id, "owned_by": m.owned_by} for m in models.data]
