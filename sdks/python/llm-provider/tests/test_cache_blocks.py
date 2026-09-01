"""Cache plumbing guards.

The typed-cache contract: a plain-str system prompt renders to the exact
bytes with no breakpoints, and cache-flagged :class:`SystemBlock`\\ s become
``cache_control`` content blocks precisely where flagged.
"""

from __future__ import annotations

from llm_provider.adapter.common import (
    supports_cache_control,
    system_blocks,
    wants_cache,
)
from llm_provider.mappers.chat_completions import (
    build_messages,
    build_system_message,
    cache_control,
    render_cache_blocks,
)
from llm_provider.schemas import (
    ImageData,
    Message,
    SystemBlock,
    ToolCallData,
    system_text,
)


def test_str_normalises_to_single_uncached_block():
    blocks = system_blocks("plain prompt")
    assert [(b.text, b.cache) for b in blocks] == [("plain prompt", False)]
    assert not wants_cache(blocks)


def test_empty_prompts_normalise_to_no_blocks():
    assert system_blocks("") == []
    assert system_blocks([]) == []
    assert system_blocks([SystemBlock(text="", cache=True)]) == []


def test_block_list_passes_through_and_drops_empties():
    blocks = system_blocks(
        [
            SystemBlock(text="static", cache=True),
            SystemBlock(text=""),
            SystemBlock(text="tail"),
        ]
    )
    assert [(b.text, b.cache) for b in blocks] == [("static", True), ("tail", False)]
    assert wants_cache(blocks)


def test_system_text_joins_and_ignores_flags():
    blocks = system_blocks([SystemBlock(text="a", cache=True), SystemBlock(text="b")])
    assert system_text(blocks) == "ab"


def test_build_system_message_renders_blocks_when_ttl_and_flagged():
    blocks = [SystemBlock(text="static", cache=True), SystemBlock(text="tail")]
    assert build_system_message(blocks, cache_ttl="5m") == {
        "role": "system",
        "content": [
            {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "tail"},
        ],
    }


def test_build_system_message_plain_text_without_ttl():
    # cache_ttl=None is the caller saying "this model can't take cache_control"
    # — flags are ignored and the exact joined bytes go out.
    blocks = [SystemBlock(text="static", cache=True), SystemBlock(text="tail")]
    assert build_system_message(blocks) == {"role": "system", "content": "statictail"}


def test_build_system_message_plain_text_when_nothing_flagged():
    blocks = [SystemBlock(text="just a prompt")]
    assert build_system_message(blocks, cache_ttl="5m") == {
        "role": "system",
        "content": "just a prompt",
    }


def test_cache_control_tiers():
    # Bare form (default 5m) is what Bedrock accepts everywhere; the explicit
    # ttl field is the 1h extended-cache beta only.
    assert cache_control("5m") == {"type": "ephemeral"}
    assert cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}
    # Unknown tiers degrade to the bare form rather than a rejected request.
    assert cache_control("2h") == {"type": "ephemeral"}


def test_render_attaches_breakpoints_only_where_flagged():
    blocks = [SystemBlock(text="static", cache=True), SystemBlock(text="tail")]
    assert render_cache_blocks(blocks, "1h") == [
        {
            "type": "text",
            "text": "static",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {"type": "text", "text": "tail"},
    ]


def test_render_supports_multiple_breakpoints():
    # e.g. one breakpoint after static rules, one after a template appendix.
    blocks = [
        SystemBlock(text="rules", cache=True),
        SystemBlock(text="appendix", cache=True),
        SystemBlock(text="volatile"),
    ]
    out = render_cache_blocks(blocks, "5m")
    assert [("cache_control" in b) for b in out] == [True, True, False]


def test_supports_cache_control_families():
    assert supports_cache_control("us.anthropic.claude-opus-4-1")
    assert supports_cache_control("claude-sonnet-5")
    assert supports_cache_control("gemini-2.5-pro")
    # OpenAI caches automatically by prefix and rejects cache_control blocks.
    assert not supports_cache_control("gpt-4o")
    assert not supports_cache_control("")


def test_message_cache_promotes_last_string_message():
    out = build_messages([Message(role="user", content="hi")], cache_ttl="5m")
    assert out[-1]["content"] == [
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
    ]


def test_message_cache_attaches_to_last_part_of_multipart_content():
    # Vision turns carry list content; the breakpoint rides the LAST part.
    out = build_messages(
        [
            Message(
                role="user",
                content="look",
                images=[ImageData(mime_type="image/png", base64="QUJD")],
            )
        ],
        cache_ttl="5m",
    )
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in out[-1]["content"][0]


def test_message_cache_skips_back_past_uncacheable_tail():
    # A trailing tool-calls-only turn (content None) can't carry the
    # breakpoint — it lands on the nearest earlier message that can.
    out = build_messages(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                tool_calls=[ToolCallData(id="c1", name="T", arguments="{}")],
            ),
        ],
        cache_ttl="5m",
    )
    assert out[-1]["content"] is None
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_message_cache_noop_when_no_message_can_carry_it():
    out = build_messages(
        [
            Message(
                role="assistant",
                tool_calls=[ToolCallData(id="c1", name="T", arguments="{}")],
            )
        ],
        cache_ttl="5m",
    )
    assert out[-1]["content"] is None


def test_no_cache_ttl_leaves_messages_untouched():
    out = build_messages([Message(role="user", content="hi")])
    assert out == [{"role": "user", "content": "hi"}]
