"""``LLMAdapter.stream`` — the Responses-API path, routing, and ``complete``.

Pins the normalised event mapping for every Responses event we handle, the
intent-based routing between the two wire APIs, and ``complete()``'s
buffer-text-forward-status contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from llm_provider.adapter import LLMAdapter
from llm_provider.schemas import ChatRequest, Message, SystemBlock

pytestmark = pytest.mark.asyncio


def _ev(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def _completed(*, input_tokens=0, output_tokens=0, cached=None, cache_write=None, output=()):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_tokens_details=(
            SimpleNamespace(cached_tokens=cached) if cached is not None else None
        ),
        cache_creation_input_tokens=cache_write,
    )
    return _ev(
        "response.completed",
        response=SimpleNamespace(usage=usage, output=list(output)),
    )


def _responses_adapter(events, captured=None):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        if captured is not None:
            captured.update(kwargs)

        async def _gen():
            for e in events:
                yield e

        return _gen()

    adapter.client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    return adapter


def _dual_adapter(resp_events, chat_chunks):
    """Adapter exposing BOTH wire APIs; records which one served the request."""
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")
    served = {"responses": 0, "chat": 0}
    captured: dict = {}

    async def _resp_create(**kwargs):
        served["responses"] += 1
        captured.update(kwargs)

        async def _gen():
            for e in resp_events:
                yield e

        return _gen()

    async def _chat_create(**kwargs):
        served["chat"] += 1
        captured.update(kwargs)

        async def _gen():
            for c in chat_chunks:
                yield c

        return _gen()

    adapter.client = SimpleNamespace(
        responses=SimpleNamespace(create=_resp_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=_chat_create)),
    )
    return adapter, served, captured


def _chat_ok_chunks():
    return [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)),
    ]


# ------------------------------------------------------------ event mapping


async def test_responses_stream_maps_text_thinking_tools_and_usage():
    events = [
        _ev("response.output_text.delta", delta="Hi "),
        _ev("response.reasoning_text.delta", delta="think"),
        _ev("response.reasoning_summary_text.delta", delta="sum"),
        _ev(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", id="item1", call_id="call_1", name="MakeSlide"),
        ),
        _ev("response.function_call_arguments.delta", item_id="item1", delta='{"a"'),
        _ev("response.function_call_arguments.delta", item_id="item1", delta=":1}"),
        _ev("response.function_call_arguments.done", item_id="item1", arguments='{"a":1}'),
        _ev("response.some.future.event"),  # unknown — must be ignored
        _completed(input_tokens=10, output_tokens=5, cached=4, cache_write=2),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    evs = [ev async for ev in adapter.stream(req, "sys")]
    kinds = [e.event for e in evs]

    assert kinds == [
        "text_delta",
        "thinking_delta",
        "thinking_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_done",
        "done",
    ]
    start = evs[3]
    assert start.data == {"call_id": "call_1", "name": "MakeSlide"}
    done_tc = evs[6]
    assert done_tc.data == {"call_id": "call_1", "name": "MakeSlide", "arguments": '{"a":1}'}
    assert evs[-1].data["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 4,
        "cache_write_tokens": 2,
    }


async def test_responses_stream_web_search_flow_and_sources():
    ann = SimpleNamespace(type="url_citation", url="https://a.example", title="A")
    dup = SimpleNamespace(type="url_citation", url="https://a.example", title="A again")
    msg_item = SimpleNamespace(type="message", content=[SimpleNamespace(annotations=[ann, dup])])
    events = [
        _ev("response.web_search_call.in_progress"),
        _ev("response.web_search_call.searching"),
        _ev("response.web_search_call.completed"),
        _completed(input_tokens=1, output_tokens=1, output=[msg_item]),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    evs = [ev async for ev in adapter.stream(req, "sys")]
    kinds = [e.event for e in evs]

    assert kinds == [
        "web_search_start",
        "web_search_searching",
        "web_search_done",
        "web_search_sources",
        "done",
    ]
    # Citations deduped by URL, first-seen title wins.
    assert evs[3].data == {"sources": [{"url": "https://a.example", "title": "A"}]}


async def test_responses_stream_soft_failure_yields_error_not_raise():
    events = [
        _ev("response.output_text.delta", delta="partial"),
        _ev("response.failed", response=SimpleNamespace(error=SimpleNamespace(message="boom"))),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    evs = [ev async for ev in adapter.stream(req, "sys")]

    # Partial output preserved; error surfaced as an event; stream still
    # terminates with done (zero usage — no completed event arrived).
    assert [e.event for e in evs] == ["text_delta", "error", "done"]
    assert evs[1].data == {"message": "boom"}


async def test_responses_stream_request_shape():
    captured: dict = {}
    adapter = _responses_adapter([_completed()], captured)
    req = ChatRequest(
        model="m",
        messages=[Message(role="user", content="q")],
        tools=[{"type": "function", "name": "T", "description": "", "parameters": {}}],
        thinking=True,
        max_output_tokens=777,
    )

    _ = [ev async for ev in adapter.stream(req, "sys")]

    assert captured["model"] == "m"
    assert captured["instructions"] == "sys"
    assert captured["stream"] is True
    assert captured["max_output_tokens"] == 777
    assert captured["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert captured["tools"][0]["name"] == "T"


async def test_responses_stream_omits_optional_kwargs():
    captured: dict = {}
    adapter = _responses_adapter([_completed()], captured)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    _ = [ev async for ev in adapter.stream(req, "sys")]

    assert "tools" not in captured
    assert "reasoning" not in captured
    assert "max_output_tokens" not in captured


# ------------------------------------------------------------------ routing


async def test_plain_prompt_routes_to_responses():
    adapter, served, _ = _dual_adapter([_completed()], _chat_ok_chunks())
    req = ChatRequest(model="claude-x", messages=[Message(role="user", content="q")])
    _ = [ev async for ev in adapter.stream(req, "plain system")]
    assert served == {"responses": 1, "chat": 0}


async def test_cache_flagged_blocks_route_to_chat():
    adapter, served, _ = _dual_adapter([_completed()], _chat_ok_chunks())
    req = ChatRequest(model="claude-x", messages=[Message(role="user", content="q")])
    system = [SystemBlock(text="S", cache=True), SystemBlock(text="t")]
    _ = [ev async for ev in adapter.stream(req, system)]
    assert served == {"responses": 0, "chat": 1}


async def test_message_cache_ttl_alone_routes_to_chat():
    """History-only caching: no flagged blocks, but message_cache_ttl set —
    must still reach the chat path (inexpressible under the old design)."""
    adapter, served, captured = _dual_adapter([_completed()], _chat_ok_chunks())
    req = ChatRequest(
        model="claude-x",
        messages=[Message(role="user", content="q")],
        message_cache_ttl="5m",
    )
    _ = [ev async for ev in adapter.stream(req, "plain system")]
    assert served == {"responses": 0, "chat": 1}
    # System stays plain text (no flags), but the last message carries the
    # moving history breakpoint.
    assert captured["messages"][0]["content"] == "plain system"
    assert captured["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------- complete()


async def test_complete_buffers_text_and_forwards_status():
    events = [
        _ev(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call", id="i1", call_id="c1", name="T"),
        ),
        _ev("response.function_call_arguments.done", item_id="i1", arguments="{}"),
        _ev("response.output_text.delta", delta="Hel"),
        _ev("response.output_text.delta", delta="lo"),
        _completed(input_tokens=7, output_tokens=3, cached=5),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    evs = [ev async for ev in adapter.complete(req, "sys")]
    kinds = [e.event for e in evs]

    # Tool events forwarded live; text collapsed to ONE event before done.
    assert kinds == ["tool_call_start", "tool_call_done", "text", "done"]
    assert evs[2].data == {"text": "Hello"}
    # Full usage (incl. cache counters) survives the wrapper.
    assert evs[3].data["usage"] == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_read_tokens": 5,
        "cache_write_tokens": 0,
    }


async def test_complete_emits_no_text_event_when_no_text():
    adapter = _responses_adapter([_completed()])
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])
    kinds = [ev.event async for ev in adapter.complete(req, "sys")]
    assert kinds == ["done"]
