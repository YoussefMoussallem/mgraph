"""The Chat Completions call paths (``chat.completions.create``).

The caching dialect: LiteLLM honors content-block ``cache_control`` on
``/chat/completions`` (it strips it during the Responses transform), so the
facade routes every request with caching intent to :func:`stream` here.
:func:`generate_chat_completion` serves short utility generations (titles,
labels, vision describes) where the Responses API may be unavailable.
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
    supports_cache_control,
    system_blocks,
    trace_attributes,
    translate_provider_errors,
)
from llm_provider.mappers.chat_completions import (
    build_messages,
    build_system_message,
    build_tools,
)
from llm_provider.schemas import ChatRequest, StreamEvent, SystemBlock

logger = logging.getLogger(__name__)


def _tool_dones(tool_calls: dict[int, dict], done_indices: set[int]) -> list[StreamEvent]:
    """Build ``tool_call_done`` events for any not-yet-closed tool calls.

    Idempotent via ``done_indices`` so the finish_reason path and the
    trailing flush can both call it without double-emitting. Emits in index
    order for deterministic multi-tool turns. A call whose identity never
    arrived (no fragment carried id/name, so no start was emitted) gets its
    start here so consumers always see a paired start → done.
    """
    events: list[StreamEvent] = []
    for idx in sorted(tool_calls):
        if idx in done_indices:
            continue
        done_indices.add(idx)
        entry = tool_calls[idx]
        if not entry.get("started"):
            entry["started"] = True
            events.append(
                StreamEvent(
                    "tool_call_start",
                    {"call_id": entry["call_id"], "name": entry["name"]},
                )
            )
        events.append(
            StreamEvent(
                "tool_call_done",
                {
                    "call_id": entry["call_id"],
                    "name": entry["name"],
                    "arguments": entry["args"],
                },
            )
        )
    return events


async def stream(
    client: openai.AsyncOpenAI,
    request: ChatRequest,
    blocks: list[SystemBlock],
    *,
    reasoning_effort: str,
    cost_fn: CostFn | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream the main agent loop over ``chat.completions``.

    Emits the same normalised :class:`StreamEvent`\\ s as the Responses
    path (``text_delta``, ``thinking_delta``, ``tool_call_*``, ``error``,
    ``done`` with cache-token counts). Hosted ``web_search_*`` events don't
    occur here — the agent reaches web search through a function tool on a
    secondary model, which keeps its own Responses path.

    Tool calls stream differently than on Responses and need care:
    chat.completions sends ``delta.tool_calls`` fragments keyed by array
    **index** (the ``id`` and function name arrive on the first fragment,
    the JSON ``arguments`` stream across the rest), and there is **no**
    per-call "done" event — completion is signalled by ``finish_reason``.
    We accumulate fragments by index and flush ``tool_call_done`` when the
    finish arrives (plus a trailing flush in case the proxy omits the
    finish on the usage-only final chunk).
    """
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    text_parts: list[str] = []
    # index -> {"call_id", "name", "args", "started", "flushed"}:
    # chat.completions keys streamed tool-call fragments by their position
    # in the array, not an item id. ``started``/``flushed`` defer start and
    # delta emission until the call's identity (id/name) has arrived.
    tool_calls: dict[int, dict] = {}
    done_indices: set[int] = set()

    # System prompt first (cache-flagged blocks render as cache_control
    # content — the shape LiteLLM forwards to Bedrock's cachePoint /
    # Gemini context caching), then the conversation. Models that don't
    # take cache_control (OpenAI caches automatically) get plain text.
    cacheable = supports_cache_control(request.model)
    messages: list[dict] = [
        build_system_message(
            blocks, cache_ttl=request.cache_ttl if cacheable else None
        )
    ]
    messages.extend(
        build_messages(
            request.messages,
            # Cache the conversation too: a breakpoint on the final message
            # caches the system→history prefix at the shorter message tier.
            cache_ttl=request.message_cache_ttl if cacheable else None,
        )
    )

    kwargs: dict = {
        "model": request.model,
        "messages": messages,
        "stream": True,
        # Without include_usage the stream carries no usage object, so the
        # cache-token counts would never arrive — the whole point here.
        "stream_options": {"include_usage": True},
    }
    chat_tools = build_tools(request.tools)
    if chat_tools:
        kwargs["tools"] = chat_tools
    # Same cap as the Responses path — without it the provider's low default
    # truncates large tool-call JSON mid-stream (WriteArtifact with big HTML).
    if request.max_output_tokens is not None:
        kwargs["max_tokens"] = request.max_output_tokens
    if request.thinking:
        # LiteLLM maps reasoning_effort -> the provider's thinking budget;
        # the thinking text streams back as ``delta.reasoning_content``.
        kwargs["reasoning_effort"] = reasoning_effort

    trace_input = {k: v for k, v in kwargs.items() if k not in ("stream", "stream_options")}
    gen_ctx = langfuse_generation(
        "llm-stream",
        request.model,
        input_data=trace_input,
        **request_trace_attributes(request),
    )
    gen_obs = gen_ctx.__enter__() if gen_ctx else None

    try:
        with translate_provider_errors():
            stream_resp = await client.chat.completions.create(**kwargs)

            async for chunk in stream_resp:
                # Usage rides the final chunk (include_usage); ``choices`` is
                # then typically empty, so read usage before touching choices.
                usage = getattr(chunk, "usage", None)
                if usage:
                    input_tokens = as_int(getattr(usage, "prompt_tokens", 0))
                    output_tokens = as_int(getattr(usage, "completion_tokens", 0))
                    cache_read, cache_write = extract_cache_tokens(usage)

                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)

                if delta is not None:
                    content = getattr(delta, "content", None)
                    if content:
                        text_parts.append(content)
                        yield StreamEvent("text_delta", {"text": content})

                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield StreamEvent("thinking_delta", {"text": reasoning})

                    for tc in getattr(delta, "tool_calls", None) or []:
                        idx = tc.index
                        fn = getattr(tc, "function", None)
                        entry = tool_calls.get(idx)
                        if entry is None:
                            entry = tool_calls[idx] = {
                                "call_id": "",
                                "name": "",
                                "args": "",
                                "started": False,
                                "flushed": 0,
                            }
                        # id / name can be split across fragments — backfill.
                        if getattr(tc, "id", None) and not entry["call_id"]:
                            entry["call_id"] = tc.id
                        if fn and getattr(fn, "name", None) and not entry["name"]:
                            entry["name"] = fn.name
                        frag = getattr(fn, "arguments", None) if fn else None
                        if frag:
                            entry["args"] += frag
                        # Defer start until the call has an identity: starting
                        # with an empty call_id that later fragments backfill
                        # would hand consumers mismatched correlation keys.
                        if not entry["started"] and (entry["call_id"] or entry["name"]):
                            entry["started"] = True
                            yield StreamEvent(
                                "tool_call_start",
                                {"call_id": entry["call_id"], "name": entry["name"]},
                            )
                        # Flush any args beyond what's already been emitted —
                        # including fragments buffered while identity was
                        # pending — as one delta.
                        if entry["started"] and len(entry["args"]) > entry["flushed"]:
                            pending = entry["args"][entry["flushed"] :]
                            entry["flushed"] = len(entry["args"])
                            yield StreamEvent(
                                "tool_call_delta",
                                {"call_id": entry["call_id"], "delta": pending},
                            )

                # A finish_reason closes the assistant turn; chat.completions has
                # no per-call done event, so flush accumulated tool calls here.
                if getattr(choice, "finish_reason", None):
                    for ev in _tool_dones(tool_calls, done_indices):
                        yield ev

        # Clean completion (translated errors raised out of the ``with``).
        # Trailing flush: if the stream ended without a finish_reason (some
        # proxies omit it on the usage-only final chunk) close any open calls.
        for ev in _tool_dones(tool_calls, done_indices):
            yield ev

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
                if tool_calls:
                    # The tool calls this turn made — visible on the trace
                    # without digging through raw output.
                    update_kwargs["metadata"] = {
                        "tool_calls": [
                            {"call_id": e["call_id"], "name": e["name"]}
                            for _, e in sorted(tool_calls.items())
                        ]
                    }
                gen_obs.update(**update_kwargs)
            except Exception:
                logger.debug("Langfuse generation update failed", exc_info=True)
    finally:
        # Runs on success, provider errors, AND consumer abandonment
        # (GeneratorExit) — the Langfuse observation must never leak.
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


async def generate_chat_completion(
    client: openai.AsyncOpenAI,
    *,
    model: str,
    system_prompt: str | list[SystemBlock],
    user_content: str | list[dict],
    temperature: float = 0.3,
    temperature_unsupported: set[str],
    cache_ttl: str = "1h",
    return_usage: bool = False,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_metadata: dict | None = None,
    trace_tags: list[str] | None = None,
    cost_fn: CostFn | None = None,
) -> str | tuple[str, dict]:
    """Single non-streaming Chat Completions call — plain text in/out.

    Returns the assistant's ``content`` string (stripped), or empty string
    if the model returned no text. Does **not** support tools or reasoning —
    only system + user messages. Cache-flagged :class:`SystemBlock`\\ s
    become ``cache_control`` breakpoints on supporting models.

    ``temperature_unsupported`` is the caller-owned memo of models that
    rejected ``temperature`` — mutated in place when a 400 teaches us a new
    one, so subsequent calls skip the doomed first attempt.

    ``user_id`` / ``session_id`` / ``trace_metadata`` / ``trace_tags`` are
    Langfuse trace attributes — never sent to the provider.
    """
    blocks = system_blocks(system_prompt)
    kwargs: dict = {
        "model": model,
        "messages": [
            build_system_message(
                blocks,
                cache_ttl=cache_ttl if supports_cache_control(model) else None,
            ),
            {"role": "user", "content": user_content},
        ],
    }
    if model not in temperature_unsupported:
        kwargs["temperature"] = temperature

    gen_ctx = langfuse_generation(
        "llm-chat-completion",
        model,
        input_data=dict(kwargs),
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
                response = await client.chat.completions.create(**kwargs)
            except openai.APIStatusError as first:
                # Newer Claude models (e.g. Opus 4.x on Bedrock) hard-400 on
                # ``temperature``. Drop the param, remember the model, retry
                # once — so a single configured model id works across
                # families and later calls skip the failed attempt entirely.
                if (
                    first.status_code == 400
                    and "temperature" in kwargs
                    and "temperature" in (first.message or "").lower()
                ):
                    temperature_unsupported.add(model)
                    kwargs.pop("temperature", None)
                    response = await client.chat.completions.create(**kwargs)
                else:
                    raise
    except BaseException as e:
        # Close the observation with the propagating exception — tracing
        # must never leak on failures.
        if gen_ctx:
            gen_ctx.__exit__(type(e), e, e.__traceback__)
        raise

    choice = response.choices[0] if response.choices else None
    msg = getattr(choice, "message", None) if choice else None
    result = (getattr(msg, "content", None) or "").strip()

    usage = getattr(response, "usage", None)
    if gen_obs:
        try:
            usage_details = {
                "input": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output": getattr(usage, "completion_tokens", 0) if usage else 0,
            }
            update_kwargs: dict = {"output": result, "usage_details": usage_details}
            if costs := cost_details(cost_fn, model, usage_details):
                update_kwargs["cost_details"] = costs
            gen_obs.update(**update_kwargs)
        except Exception:
            logger.debug("Langfuse generation update failed", exc_info=True)
    if gen_ctx:
        gen_ctx.__exit__(None, None, None)

    if return_usage:
        # Normalised usage for the caller's accounting (e.g. the slide
        # writer records a ``slide_gen`` row). cache_read / cache_write come
        # from the LiteLLM→Anthropic passthrough; 0 when the model/provider
        # doesn't cache.
        cache_read, cache_write = extract_cache_tokens(usage)
        usage_out = {
            "input_tokens": as_int(getattr(usage, "prompt_tokens", 0)) if usage else 0,
            "output_tokens": as_int(getattr(usage, "completion_tokens", 0)) if usage else 0,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }
        return result, usage_out
    return result
