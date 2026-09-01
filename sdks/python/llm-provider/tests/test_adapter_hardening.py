"""Guards for the hardening fixes layered onto the extracted SDK.

These pin behaviour the edwin-embedded copy did not: Langfuse observation
release when a consumer abandons a stream mid-flight, usage-less terminal
events, ``generate()`` honouring the output-token cap, tool_call_start
deferred until the call's identity is known, and the hosted-tool drop
warning on the chat path.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import llm_provider.adapter.chat_completions as adapter_chat
import llm_provider.adapter.responses as adapter_responses
import pytest
from llm_provider.adapter import LLMAdapter
from llm_provider.adapter.common import system_blocks
from llm_provider.mappers.chat_completions import build_tools
from llm_provider.schemas import ChatRequest, Message, SystemBlock


class _FakeCtx:
    """Langfuse-shaped context manager recording enter/exit for assertions."""

    def __init__(self):
        self.entered = False
        self.exit_args = None

    def __enter__(self):
        self.entered = True
        return SimpleNamespace(update=lambda **kw: None)

    def __exit__(self, exc_type, exc, tb):
        self.exit_args = (exc_type, exc, tb)
        return False


def _responses_adapter(events, captured=None):
    """Adapter whose ``responses.create`` streams ``events`` (or returns the
    single ``events`` object when it isn't a list — the non-streaming shape)."""
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        if not isinstance(events, list):
            return events

        async def _gen():
            for e in events:
                yield e

        return _gen()

    adapter.client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    return adapter


def _chat_adapter(chunks):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    adapter.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    return adapter


def _delta(*, content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
    )


def _choice(delta, finish_reason=None):
    return SimpleNamespace(delta=delta, finish_reason=finish_reason)


def _chunk(choices, usage=None):
    return SimpleNamespace(choices=choices, usage=usage)


def _tool_frag(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ---------------------------------------------------------------- generate()


@pytest.mark.asyncio
async def test_generate_forwards_max_output_tokens():
    captured: dict = {}
    adapter = _responses_adapter(SimpleNamespace(output=[], usage=None), captured)
    req = ChatRequest(
        model="m",
        messages=[Message(role="user", content="hi")],
        max_output_tokens=4096,
    )
    await adapter.generate(req, system_prompt="s")
    assert captured["max_output_tokens"] == 4096


@pytest.mark.asyncio
async def test_generate_omits_cap_when_unset():
    captured: dict = {}
    adapter = _responses_adapter(SimpleNamespace(output=[], usage=None), captured)
    req = ChatRequest(model="m", messages=[Message(role="user", content="hi")])
    await adapter.generate(req, system_prompt="s")
    assert "max_output_tokens" not in captured


# ------------------------------------------------- Langfuse context release


@pytest.mark.asyncio
async def test_responses_stream_releases_langfuse_ctx_on_abandon(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(adapter_responses, "langfuse_generation", lambda *a, **kw: ctx)
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="one"),
        SimpleNamespace(type="response.output_text.delta", delta="two"),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="hi")])

    agen = adapter.stream(req, "plain system")  # unmarked → Responses path
    assert (await agen.__anext__()).event == "text_delta"
    await agen.aclose()  # consumer walks away mid-stream

    assert ctx.entered
    assert ctx.exit_args is not None
    assert ctx.exit_args[0] is GeneratorExit


@pytest.mark.asyncio
async def test_chat_stream_releases_langfuse_ctx_on_abandon(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(adapter_chat, "langfuse_generation", lambda *a, **kw: ctx)
    chunks = [
        _chunk([_choice(_delta(content="one"))]),
        _chunk([_choice(_delta(content="two"))]),
    ]
    adapter = _chat_adapter(chunks)
    req = ChatRequest(model="claude-x", messages=[Message(role="user", content="hi")])

    # Cache-flagged blocks → routed to the chat.completions path.
    agen = adapter.stream(req, [SystemBlock(text="S", cache=True), SystemBlock(text="t")])
    assert (await agen.__anext__()).event == "text_delta"
    await agen.aclose()

    assert ctx.entered
    assert ctx.exit_args is not None
    assert ctx.exit_args[0] is GeneratorExit


@pytest.mark.asyncio
async def test_responses_stream_closes_ctx_cleanly_on_completion(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(adapter_responses, "langfuse_generation", lambda *a, **kw: ctx)
    events = [SimpleNamespace(type="response.output_text.delta", delta="hi")]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    _ = [ev async for ev in adapter.stream(req, "plain")]

    assert ctx.exit_args == (None, None, None)


# ------------------------------------------------------- usage-less terminal


@pytest.mark.asyncio
async def test_responses_stream_survives_missing_usage():
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="hi"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None, output=[]),
        ),
    ]
    adapter = _responses_adapter(events)
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    evs = [ev async for ev in adapter.stream(req, "plain")]

    assert evs[-1].event == "done"
    assert evs[-1].data["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


# --------------------------------------------- deferred tool_call_start id


@pytest.mark.asyncio
async def test_chat_tool_start_deferred_until_identity_arrives():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        # Args-only first fragment: no id/name yet — nothing may be emitted,
        # or consumers would correlate on an empty call_id.
        _chunk([_choice(_delta(tool_calls=[_tool_frag(0, arguments='{"a":')]))]),
        # Identity arrives: start fires with the real id and the buffered
        # fragment flushes together with the new one.
        _chunk(
            [
                _choice(
                    _delta(
                        tool_calls=[_tool_frag(0, id="call_9", name="Foo", arguments="1}")]
                    )
                )
            ]
        ),
        _chunk([_choice(_delta(), finish_reason="tool_calls")]),
        _chunk([], usage=usage),
    ]
    adapter = _chat_adapter(chunks)
    req = ChatRequest(model="m", messages=[Message(role="user", content="x")])

    evs = [
        ev
        async for ev in adapter_chat.stream(
            adapter.client,
            req,
            system_blocks("system text"),
            reasoning_effort=adapter.reasoning_effort,
        )
    ]

    assert [e.event for e in evs] == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_done",
        "done",
    ]
    assert evs[0].data == {"call_id": "call_9", "name": "Foo"}
    assert evs[1].data == {"call_id": "call_9", "delta": '{"a":1}'}
    assert evs[2].data["arguments"] == '{"a":1}'


@pytest.mark.asyncio
async def test_chat_flush_pairs_start_with_done_for_unidentified_call():
    """Degenerate stream where no fragment ever carries id/name: the trailing
    flush still emits a paired start → done (with the accumulated args)."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(tool_calls=[_tool_frag(0, arguments="{}")]))]),
        _chunk([], usage=usage),  # no finish_reason, identity never arrived
    ]
    adapter = _chat_adapter(chunks)
    req = ChatRequest(model="m", messages=[Message(role="user", content="x")])

    evs = [
        ev
        async for ev in adapter_chat.stream(
            adapter.client,
            req,
            system_blocks("system text"),
            reasoning_effort=adapter.reasoning_effort,
        )
    ]

    assert [e.event for e in evs] == ["tool_call_start", "tool_call_done", "done"]
    assert evs[1].data["arguments"] == "{}"


# ------------------------------------------------------ hosted-tool warning


def test_build_tools_warns_when_dropping_hosted_tool(caplog):
    with caplog.at_level(logging.WARNING, logger="llm_provider.mappers.chat_completions"):
        out = build_tools([{"type": "web_search_preview"}])
    assert out == []
    assert "web_search_preview" in caplog.text
