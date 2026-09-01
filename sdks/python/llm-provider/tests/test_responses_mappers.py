"""``build_input`` / ``build_tools`` — internal -> Responses API shapes.

The Responses counterpart of test_chat_mappers: assistant text and tool
calls become SEPARATE input items (unlike chat's single message), and tool
results become ``function_call_output`` items paired by ``call_id``.
"""

from __future__ import annotations

from llm_provider.mappers.responses import build_input, build_tools
from llm_provider.schemas import ImageData, Message, ToolCallData


def test_user_plain_text():
    out = build_input([Message(role="user", content="hi")])
    assert out == [{"role": "user", "content": "hi"}]


def test_user_images_become_multipart_data_urls():
    out = build_input(
        [
            Message(
                role="user",
                content="look",
                images=[ImageData(mime_type="image/png", base64="QUJD")],
            )
        ]
    )
    content = out[0]["content"]
    assert content[0] == {"type": "input_text", "text": "look"}
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,QUJD",
    }


def test_assistant_text_and_tool_calls_are_separate_items():
    out = build_input(
        [
            Message(
                role="assistant",
                content="on it",
                tool_calls=[ToolCallData(id="c1", name="MakeSlide", arguments="{}")],
            )
        ]
    )
    # Text first (preserving the model's original ordering), then the call.
    assert out[0] == {"role": "assistant", "content": "on it"}
    assert out[1] == {
        "type": "function_call",
        "call_id": "c1",
        "name": "MakeSlide",
        "arguments": "{}",
    }


def test_tool_result_becomes_function_call_output():
    out = build_input([Message(role="tool", tool_call_id="c1", content="done")])
    assert out == [
        {"type": "function_call_output", "call_id": "c1", "output": "done"}
    ]


def test_build_tools_wraps_functions_and_passes_builtins_through():
    web_search = {"type": "web_search_preview"}
    out = build_tools(
        [
            {
                "type": "function",
                "name": "T",
                "description": "d",
                "parameters": {"type": "object"},
            },
            web_search,
        ]
    )
    assert out[0] == {
        "type": "function",
        "name": "T",
        "description": "d",
        "parameters": {"type": "object"},
        "strict": None,
    }
    # Hosted built-ins pass through untouched — same object, no wrapping.
    assert out[1] is web_search


def test_build_tools_empty():
    assert build_tools(None) == []
    assert build_tools([]) == []
