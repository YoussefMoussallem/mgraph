"""``build_messages`` / ``build_tools`` — internal -> Chat Completions.

The careful part is tool-call history: a prior assistant turn's text and tool
calls collapse into ONE message (content + tool_calls), and each tool result
becomes a ``role: "tool"`` message paired by ``tool_call_id``. Getting this
wrong breaks multi-turn tool use on the chat path.
"""

from __future__ import annotations

from llm_provider.mappers.chat_completions import build_messages, build_tools
from llm_provider.schemas import ImageData, Message, ToolCallData


def test_user_assistant_tool_roundtrip():
    messages = [
        Message(role="user", content="make a slide"),
        Message(
            role="assistant",
            content="on it",
            tool_calls=[ToolCallData(id="c1", name="CreateSlide", arguments='{"t":1}')],
        ),
        Message(role="tool", tool_call_id="c1", content="ok, slide 1"),
        Message(role="assistant", content="done"),
    ]
    out = build_messages(messages)

    assert out[0] == {"role": "user", "content": "make a slide"}
    # assistant text + tool_calls collapse into ONE message
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] == "on it"
    assert out[1]["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "CreateSlide", "arguments": '{"t":1}'},
        }
    ]
    # tool result pairs back by tool_call_id
    assert out[2] == {"role": "tool", "tool_call_id": "c1", "content": "ok, slide 1"}
    assert out[3] == {"role": "assistant", "content": "done"}


def test_tool_calls_only_turn_sends_null_content():
    # A tool-calls-only assistant turn must send content: null (not "") so
    # Bedrock doesn't reject an empty turn.
    messages = [
        Message(
            role="assistant",
            tool_calls=[ToolCallData(id="c9", name="ListSlides", arguments="{}")],
        )
    ]
    out = build_messages(messages)
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["id"] == "c9"


def test_user_images_become_multipart():
    messages = [
        Message(
            role="user",
            content="look",
            images=[ImageData(mime_type="image/png", base64="QUJD")],
        )
    ]
    out = build_messages(messages)
    content = out[0]["content"]
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_build_tools_nests_function_and_skips_builtins():
    tools = [
        {
            "type": "function",
            "name": "CreateSlide",
            "description": "d",
            "parameters": {"type": "object"},
        },
        {"type": "web_search_preview"},  # hosted built-in — unsupported on chat
        {"type": "function", "name": "NoParams"},  # missing parameters
    ]
    out = build_tools(tools)

    assert len(out) == 2  # built-in skipped
    assert out[0] == {
        "type": "function",
        "function": {
            "name": "CreateSlide",
            "description": "d",
            "parameters": {"type": "object"},
        },
    }
    # missing parameters -> empty object schema (the API requires one)
    assert out[1]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert out[1]["function"]["name"] == "NoParams"


def test_empty_tools_is_empty_list():
    assert build_tools(None) == []
    assert build_tools([]) == []
