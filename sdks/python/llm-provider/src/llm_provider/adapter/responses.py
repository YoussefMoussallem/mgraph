"""The Responses API call paths (``responses.create``).

The default dialect: hosted web search and utility streaming run here, and
:func:`generate` serves non-streaming single shots. Caching never happens on
this endpoint — the facade routes every request with caching intent to
:mod:`.chat_completions` — so the system prompt always rides as plain-text
``instructions``.
"""

import logging
import sys
from collections.abc import AsyncIterator

import openai
from langfuse_client import generation as langfuse_generation

from llm_provider.adapter.common import (
    CostFn,
    as_int,
    cost_details,
    extract_cache_tokens,
    request_trace_attributes,
    system_blocks,
    translate_provider_errors,
)
from llm_provider.mappers.responses import build_input, build_tools
from llm_provider.schemas import ChatRequest, StreamEvent, SystemBlock, system_text

logger = logging.getLogger(__name__)


def _extract_url_citations(response) -> list[dict]:
    """Collect deduped ``url_citation`` annotations from a final Responses
    object — the sources a ``web_search`` call cited.

    Returns ``[{"url", "title"}]`` in first-seen order; empty when the
    response carried none. Defensive throughout: the SDK shape varies by
    model/version and a missing field must degrade to "no sources", never
    raise mid-stream.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            for ann in getattr(block, "annotations", None) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                url = getattr(ann, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    out.append({"url": url, "title": getattr(ann, "title", "") or ""})
    return out


async def stream(
    client: openai.AsyncOpenAI,
    request: ChatRequest,
    blocks: list[SystemBlock],
    *,
    reasoning_effort: str,
    cost_fn: CostFn | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream over ``responses.create`` as normalised :class:`StreamEvent`\\ s.

    The Responses API emits many low-level event types; this collapses them
    into the stable shape the rest of the codebase consumes (``text_delta``,
    ``thinking_delta``, ``tool_call_*``, ``web_search_*``, ``error``,
    ``done``). Unknown event types are logged at DEBUG and ignored so SDK
    upgrades don't break callers.

    The Langfuse context is opened manually (``__enter__`` / ``__exit__``)
    rather than with ``async with`` because generator functions can't nest a
    context manager around a ``yield`` cleanly; the ``finally`` closes it on
    success, provider errors, and early termination of the consumer alike
    (``GeneratorExit`` when a client disconnects mid-stream).

    Function-call arguments are streamed as tokens; we track them by
    ``item_id`` (which is opaque and stable within one response) so each
    delta can be attributed to the right logical ``call_id``.
    """
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    text_parts: list[str] = []
    # TEMP verify: did this stream actually run a web search? Lets the
    # probe in response.completed tell "no sources" apart from "model
    # emitted none". Remove together with that probe once decided.
    web_searched = False
    # item_id -> (call_id, name): item_id is the SDK's transient handle,
    # call_id is the durable identifier the rest of the app references.
    func_calls: dict[str, tuple[str, str]] = {}

    built_tools = build_tools(request.tools)
    input_items = build_input(request.messages)
    # Only cache-less prompts reach this path (caching intent routes to
    # chat.completions), so the system prompt rides in ``instructions`` as
    # plain text.
    instructions = system_text(blocks)
    kwargs: dict = {
        "model": request.model,
        "instructions": instructions,
        "input": input_items,
        "stream": True,
    }
    if built_tools:
        kwargs["tools"] = built_tools
    # Without this the provider applies a low default and truncates large
    # tool calls (e.g. a big WriteArtifact) mid-arguments.
    if request.max_output_tokens is not None:
        kwargs["max_output_tokens"] = request.max_output_tokens

    if request.thinking:
        kwargs["reasoning"] = {
            "effort": reasoning_effort,
            "summary": "auto",
        }

    trace_input = {k: v for k, v in kwargs.items() if k != "stream"}
    gen_ctx = langfuse_generation(
        "llm-stream",
        request.model,
        input_data=trace_input,
        **request_trace_attributes(request),
    )
    gen_obs = gen_ctx.__enter__() if gen_ctx else None

    try:
        with translate_provider_errors():
            stream_resp = await client.responses.create(**kwargs)

            async for event in stream_resp:
                match event.type:
                    case "response.output_text.delta":
                        if event.delta:
                            text_parts.append(event.delta)
                            yield StreamEvent("text_delta", {"text": event.delta})

                    case "response.reasoning_text.delta":
                        if event.delta:
                            yield StreamEvent("thinking_delta", {"text": event.delta})

                    case "response.reasoning_summary_text.delta":
                        # Reasoning summaries stream on a separate channel from
                        # raw reasoning text; both surface to the UI as
                        # "thinking" so the caller doesn't need to care.
                        if event.delta:
                            yield StreamEvent("thinking_delta", {"text": event.delta})

                    case "response.output_item.added":
                        item = event.item
                        if getattr(item, "type", None) == "function_call":
                            item_id = item.id
                            call_id = item.call_id
                            name = item.name
                            func_calls[item_id] = (call_id, name)
                            yield StreamEvent(
                                "tool_call_start",
                                {
                                    "call_id": call_id,
                                    "name": name,
                                },
                            )

                    case "response.function_call_arguments.delta":
                        item_id = event.item_id
                        if event.delta and item_id in func_calls:
                            call_id = func_calls[item_id][0]
                            yield StreamEvent(
                                "tool_call_delta",
                                {
                                    "call_id": call_id,
                                    "delta": event.delta,
                                },
                            )

                    case "response.function_call_arguments.done":
                        item_id = event.item_id
                        if item_id in func_calls:
                            call_id, name = func_calls[item_id]
                            yield StreamEvent(
                                "tool_call_done",
                                {
                                    "call_id": call_id,
                                    "name": name,
                                    "arguments": event.arguments,
                                },
                            )

                    case "response.web_search_call.in_progress":
                        yield StreamEvent("web_search_start", {})

                    case "response.web_search_call.searching":
                        yield StreamEvent("web_search_searching", {})

                    case "response.web_search_call.completed":
                        web_searched = True
                        yield StreamEvent("web_search_done", {})

                    case "response.completed":
                        # Usage is only available on the terminal event;
                        # cache it for the final ``done`` envelope. Guarded so
                        # a proxy that omits usage degrades to zero counts
                        # instead of an AttributeError mid-stream.
                        usage = getattr(event.response, "usage", None)
                        if usage is not None:
                            input_tokens = as_int(getattr(usage, "input_tokens", 0))
                            output_tokens = as_int(getattr(usage, "output_tokens", 0))
                            cache_read, cache_write = extract_cache_tokens(usage)
                        # web_search citations live on the final response's
                        # output annotations; surface them so callers (e.g.
                        # the WebSearch tool) can return the sources searched.
                        sources = _extract_url_citations(event.response)
                        if web_searched:
                            # TEMP verify probe (remove once decided): if this
                            # logs 0 but the answer/result text clearly cites
                            # URLs, the model emits citations as text only and
                            # we need the text-scrape fallback.
                            logger.warning(
                                "[web_search verify] ran a search; extracted "
                                "%d url_citation annotation(s)",
                                len(sources),
                            )
                        if sources:
                            yield StreamEvent("web_search_sources", {"sources": sources})

                    case "response.failed":
                        # Provider reported a soft failure mid-stream — surface
                        # the message but don't raise, so partial output is
                        # still preserved for the caller.
                        err = "Unknown error"
                        resp = getattr(event, "response", None)
                        if resp:
                            error_obj = getattr(resp, "error", None)
                            if error_obj:
                                err = getattr(error_obj, "message", str(error_obj))
                        yield StreamEvent("error", {"message": err})

                    case _ if event.type not in (
                        # Known-and-ignored events: emitted by the SDK but
                        # don't map to anything the UI needs to render.
                        "response.created",
                        "response.in_progress",
                        "response.output_item.done",
                        "response.content_part.added",
                        "response.content_part.done",
                        "response.output_text.done",
                        # Citations are read from the final response object in
                        # the response.completed case, not streamed per-token.
                        "response.output_text.annotation.added",
                        "response.reasoning_text.done",
                        "response.reasoning_summary_text.done",
                        "response.reasoning_summary_part.added",
                        "response.reasoning_summary_part.done",
                        "response.queued",
                    ):
                        logger.debug("Unhandled stream event: %s", event.type)

        # Reaching here means the stream completed cleanly (translated errors
        # raised out of the ``with`` above). Record the aggregated output +
        # token usage; wrapped in try/except because tracing must never take
        # down a successful request.
        if gen_obs:
            try:
                usage_details = {
                    "input": input_tokens,
                    "output": output_tokens,
                    # Langfuse's Anthropic-style keys so cache pricing
                    # and hit-rate dashboards work out of the box.
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                }
                update_kwargs: dict = {
                    "output": "".join(text_parts),
                    "usage_details": usage_details,
                }
                if costs := cost_details(cost_fn, request.model, usage_details):
                    update_kwargs["cost_details"] = costs
                if func_calls:
                    # The tool calls this turn made — visible on the trace
                    # without digging through raw output.
                    update_kwargs["metadata"] = {
                        "tool_calls": [
                            {"call_id": call_id, "name": name}
                            for call_id, name in func_calls.values()
                        ]
                    }
                gen_obs.update(**update_kwargs)
            except Exception:
                logger.debug("Langfuse generation update failed", exc_info=True)
    finally:
        # Runs on success, provider errors, AND consumer abandonment
        # (GeneratorExit); sys.exc_info() carries whichever exception is
        # propagating, or all-None on clean completion.
        if gen_ctx:
            gen_ctx.__exit__(*sys.exc_info())

    yield StreamEvent(
        "done",
        {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            },
        },
    )


async def generate(
    client: openai.AsyncOpenAI,
    request: ChatRequest,
    system_prompt: str | list[SystemBlock] = "",
    *,
    reasoning_effort: str,
    cost_fn: CostFn | None = None,
) -> str:
    """Single non-streaming ``responses.create`` call — assembled text out.

    Only the ``output_text`` parts of ``message`` items are concatenated;
    tool calls, reasoning, and other output types are dropped.
    """
    built_tools = build_tools(request.tools)
    input_items = build_input(request.messages)
    # This path never caches — cache flags are ignored here; callers that
    # want caching use stream() or generate_chat_completion().
    instructions = system_text(system_blocks(system_prompt))
    kwargs: dict = {
        "model": request.model,
        "instructions": instructions,
        "input": input_items,
    }
    if built_tools:
        kwargs["tools"] = built_tools
    # Same cap as the streaming paths — without it the provider's low
    # default silently truncates long generations.
    if request.max_output_tokens is not None:
        kwargs["max_output_tokens"] = request.max_output_tokens
    if request.thinking:
        kwargs["reasoning"] = {
            "effort": reasoning_effort,
            "summary": "auto",
        }

    gen_ctx = langfuse_generation(
        "llm-generate",
        request.model,
        input_data=dict(kwargs),
        **request_trace_attributes(request),
    )
    gen_obs = gen_ctx.__enter__() if gen_ctx else None

    try:
        with translate_provider_errors():
            response = await client.responses.create(**kwargs)
    except BaseException as e:
        # Close the observation with the propagating exception — tracing
        # must never leak on failures.
        if gen_ctx:
            gen_ctx.__exit__(type(e), e, e.__traceback__)
        raise

    parts: list[str] = []
    for item in response.output:
        if getattr(item, "type", None) == "message":
            for block in item.content:
                if getattr(block, "type", None) == "output_text":
                    parts.append(block.text)
    result = "".join(parts)

    if gen_obs:
        try:
            usage = getattr(response, "usage", None)
            usage_details = {
                "input": getattr(usage, "input_tokens", 0) if usage else 0,
                "output": getattr(usage, "output_tokens", 0) if usage else 0,
            }
            update_kwargs: dict = {"output": result, "usage_details": usage_details}
            if costs := cost_details(cost_fn, request.model, usage_details):
                update_kwargs["cost_details"] = costs
            gen_obs.update(**update_kwargs)
        except Exception:
            logger.debug("Langfuse generation update failed", exc_info=True)
    if gen_ctx:
        gen_ctx.__exit__(None, None, None)

    return result
