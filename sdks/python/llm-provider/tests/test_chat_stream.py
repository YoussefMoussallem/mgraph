"""``adapter.chat_completions.stream`` — the cacheable main-loop path.

The agent loop streams over chat.completions (the only path where LiteLLM
forwards content-block ``cache_control`` to Bedrock). This pins the parts that
differ from the Responses path and are easy to get wrong:

* tool calls arrive as ``delta.tool_calls`` fragments keyed by array **index**
  (id + name on the first fragment, JSON arguments split across the rest), with
  no per-call "done" event — completion is a ``finish_reason``;
* usage (incl. cache tokens) rides a final ``choices: []`` chunk that only
  appears because we send ``stream_options={"include_usage": True}``;
* the request must carry the system prompt as ``cache_control`` blocks, function
  tools nested under ``"function"``, and ``reasoning_effort`` when thinking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from llm_provider.adapter import LLMAdapter, chat_completions
from llm_provider.adapter.common import system_blocks
from llm_provider.schemas import ChatRequest, Message, SystemBlock

pytestmark = pytest.mark.asyncio


def _chat_stream(adapter, req, system):
    """Drive the chat path directly (the facade routes here on caching intent)."""
    return chat_completions.stream(
        adapter.client,
        req,
        system_blocks(system),
        reasoning_effort=adapter.reasoning_effort,
    )


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


def _stub_adapter(chunks, captured):
    """Adapter whose ``chat.completions.create`` yields ``chunks`` and records
    the kwargs it was called with into ``captured``."""
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        captured.update(kwargs)

        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    adapter.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    return adapter


async def test_chat_stream_maps_text_thinking_tools_and_usage():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        cache_creation_input_tokens=15,
    )
    chunks = [
        _chunk([_choice(_delta(content="Hello"))]),
        _chunk([_choice(_delta(reasoning_content="hmm"))]),
        # tool call: id + name on the first fragment, arguments split across two.
        _chunk(
            [
                _choice(
                    _delta(
                        tool_calls=[
                            _tool_frag(0, id="call_1", name="CreateSlide", arguments='{"ti')
                        ]
                    )
                )
            ]
        ),
        _chunk([_choice(_delta(tool_calls=[_tool_frag(0, arguments='tle":"X"}')]))]),
        _chunk([_choice(_delta(), finish_reason="tool_calls")]),
        # usage-only terminal chunk (choices empty) — only present because of
        # stream_options include_usage.
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)

    req = ChatRequest(
        model="claude-test",
        messages=[Message(role="user", content="hi")],
        tools=[
            {
                "type": "function",
                "name": "CreateSlide",
                "description": "make a slide",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        thinking=True,
        cache_ttl="5m",
    )
    system = [SystemBlock(text="STATIC RULES", cache=True), SystemBlock(text="volatile tail")]

    events = [(ev.event, ev.data) async for ev in _chat_stream(adapter, req, system)]
    kinds = [e for e, _ in events]

    # Sequence: text, thinking, one tool_call_start, two deltas, one
    # tool_call_done, then the terminal done envelope.
    assert kinds == [
        "text_delta",
        "thinking_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_done",
        "done",
    ]

    by: dict = {}
    for e, d in events:
        by.setdefault(e, []).append(d)

    assert by["text_delta"][0]["text"] == "Hello"
    assert by["thinking_delta"][0]["text"] == "hmm"
    assert by["tool_call_start"][0] == {"call_id": "call_1", "name": "CreateSlide"}
    # arguments reassembled in order from the streamed fragments.
    done_tc = by["tool_call_done"][0]
    assert done_tc["call_id"] == "call_1"
    assert done_tc["name"] == "CreateSlide"
    assert done_tc["arguments"] == '{"title":"X"}'

    # usage decomposed: net input, output, cache read + write.
    assert by["done"][0]["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_write_tokens": 15,
    }

    # Request shape: system as cache_control blocks, include_usage on, function
    # tool nested under "function", reasoning_effort set for thinking.
    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"][0]["text"] == "STATIC RULES"
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["tools"][0]["type"] == "function"
    assert captured["tools"][0]["function"]["name"] == "CreateSlide"
    assert "reasoning_effort" in captured


async def test_chat_stream_forwards_max_output_tokens_as_max_tokens():
    """The cacheable chat.completions path must honour ``max_output_tokens`` —
    the main agent loop routes here, not the Responses path."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(content="ok"), finish_reason="stop")]),
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)
    req = ChatRequest(
        model="claude-test",
        messages=[Message(role="user", content="hi")],
        max_output_tokens=16384,
    )
    system = [SystemBlock(text="RULES", cache=True), SystemBlock(text="tail")]

    _ = [ev async for ev in _chat_stream(adapter, req, system)]
    assert captured["max_tokens"] == 16384


async def test_chat_stream_closes_tool_call_without_finish_reason():
    """If the proxy omits finish_reason (some do on the usage-only chunk), the
    trailing flush still emits tool_call_done so the loop isn't left hanging."""
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=1)
    chunks = [
        _chunk(
            [
                _choice(
                    _delta(tool_calls=[_tool_frag(0, id="c1", name="ListSlides", arguments="{}")])
                )
            ]
        ),
        _chunk([], usage=usage),  # stream ends with no finish_reason anywhere
    ]
    adapter = _stub_adapter(chunks, {})
    req = ChatRequest(model="m", messages=[Message(role="user", content="x")])
    system = [SystemBlock(text="S", cache=True), SystemBlock(text="t")]

    kinds = [ev.event async for ev in _chat_stream(adapter, req, system)]
    assert kinds == ["tool_call_start", "tool_call_delta", "tool_call_done", "done"]


async def test_chat_stream_caches_last_message_when_message_ttl_set():
    """``message_cache_ttl`` attaches a 5m cache_control breakpoint to the FINAL
    conversation message (so the system→history prefix caches), while the system
    message keeps its own longer tier and earlier messages stay uncached."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(content="ok"), finish_reason="stop")]),
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)
    req = ChatRequest(
        model="claude-test",
        messages=[
            Message(role="user", content="first"),
            Message(role="assistant", content="reply"),
            Message(role="user", content="second"),
        ],
        cache_ttl="1h",
        message_cache_ttl="5m",
    )
    system = [SystemBlock(text="RULES", cache=True), SystemBlock(text="tail")]

    _ = [ev async for ev in _chat_stream(adapter, req, system)]
    msgs = captured["messages"]

    # System keeps its own 1h tier on the cached (pre-marker) block.
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # The LAST message carries the bare 5m breakpoint (string promoted to a block).
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"][-1]["text"] == "second"
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # An earlier message stays a plain string — only the tail gets a breakpoint.
    assert msgs[1]["content"] == "first"
    assert msgs[2]["content"] == "reply"


async def test_chat_stream_no_message_cache_without_ttl():
    """Without ``message_cache_ttl`` the conversation messages stay uncached."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(content="ok"), finish_reason="stop")]),
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)
    req = ChatRequest(model="claude-test", messages=[Message(role="user", content="hi")])
    system = [SystemBlock(text="R", cache=True), SystemBlock(text="t")]

    _ = [ev async for ev in _chat_stream(adapter, req, system)]
    # Last message is a plain string — no cache_control wrapping.
    assert captured["messages"][-1]["content"] == "hi"


async def test_chat_stream_strips_cache_control_for_openai():
    """OpenAI caches automatically by prefix and rejects Anthropic
    ``cache_control``. For a GPT model the system prompt is sent as PLAIN text
    (markers stripped) and the conversation carries no breakpoint — even when
    both cache TTLs are set — so the proxy never forwards a foreign field."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(content="ok"), finish_reason="stop")]),
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)
    req = ChatRequest(
        model="gpt-4o",
        messages=[Message(role="user", content="hi")],
        cache_ttl="1h",
        message_cache_ttl="5m",
    )
    system = [SystemBlock(text="RULES", cache=True), SystemBlock(text="tail")]

    _ = [ev async for ev in _chat_stream(adapter, req, system)]
    msgs = captured["messages"]
    # System is a plain string with the marker stripped — no cache_control blocks.
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "RULEStail"
    # Last message stays a plain string — no breakpoint despite message_cache_ttl.
    assert msgs[-1]["content"] == "hi"


async def test_chat_stream_keeps_cache_control_for_gemini():
    """LiteLLM forwards cache_control to Gemini (it maps onto Gemini context
    caching), so a Gemini model keeps the cache_control blocks — dropping them
    would DISABLE its caching. Only OpenAI is sent plain text."""
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    chunks = [
        _chunk([_choice(_delta(content="ok"), finish_reason="stop")]),
        _chunk([], usage=usage),
    ]
    captured: dict = {}
    adapter = _stub_adapter(chunks, captured)
    req = ChatRequest(
        model="gemini-2.5-pro",
        messages=[Message(role="user", content="hi")],
        cache_ttl="1h",
        message_cache_ttl="5m",
    )
    system = [SystemBlock(text="RULES", cache=True), SystemBlock(text="tail")]

    _ = [ev async for ev in _chat_stream(adapter, req, system)]
    msgs = captured["messages"]
    # System split into cache_control blocks (not plain text).
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Last message carries the 5m breakpoint.
    assert msgs[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
