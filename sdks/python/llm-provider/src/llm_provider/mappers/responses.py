"""Wire mappers for the Responses API (``responses.create``).

Fills the ``input`` array and the flat tool definitions. No system-prompt
shape lives here — on this endpoint it rides as the plain-text
``instructions`` kwarg (:func:`llm_provider.schemas.system_text`) — and no
``cache_control`` either: the adapter routes every caching request to
:mod:`~llm_provider.mappers.chat_completions`.
"""

from openai.types.responses import (
    EasyInputMessageParam,
    FunctionToolParam,
    ResponseFunctionToolCallParam,
    ResponseInputImageContentParam,
    ResponseInputTextContentParam,
)
from openai.types.responses.response_input_item_param import FunctionCallOutput

from llm_provider.schemas import Message


def build_input(messages: list[Message]) -> list:
    """Translate normalised :class:`Message` list into OpenAI Responses input items.

    Each role maps to a different SDK shape:

    * ``user`` with no images → plain :class:`EasyInputMessageParam`.
    * ``user`` with images → multi-part content (one text part + one image
      part per attachment) so the model sees them inline. Images are sent
      as base64 data URLs to keep requests self-contained.
    * ``assistant`` may emit text, tool calls, or both — each is a separate
      input item, matching the shape the Responses API would have produced
      itself. Emitting text first preserves the original ordering the model
      used.
    * ``tool`` turns become :class:`FunctionCallOutput` items keyed by
      ``tool_call_id`` so the provider can pair them with the originating
      tool call.
    """
    items: list = []
    for msg in messages:
        if msg.role == "user":
            if msg.images:
                parts = [
                    ResponseInputTextContentParam(
                        type="input_text",
                        text=msg.content or "",
                    )
                ]
                for img in msg.images:
                    parts.append(
                        ResponseInputImageContentParam(
                            type="input_image",
                            image_url=f"data:{img.mime_type};base64,{img.base64}",
                        )
                    )
                items.append(EasyInputMessageParam(role="user", content=parts))
            else:
                items.append(
                    EasyInputMessageParam(
                        role="user",
                        content=msg.content or "",
                    )
                )

        elif msg.role == "assistant":
            if msg.content:
                items.append(
                    EasyInputMessageParam(
                        role="assistant",
                        content=msg.content,
                    )
                )
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    items.append(
                        ResponseFunctionToolCallParam(
                            type="function_call",
                            call_id=tc.id,
                            name=tc.name,
                            arguments=tc.arguments,
                        )
                    )

        elif msg.role == "tool":
            items.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=msg.tool_call_id or "",
                    output=msg.content or "",
                )
            )

    return items


def build_tools(tools: list[dict] | None) -> list:
    """Translate tool definitions into OpenAI SDK types.

    Tools with a ``type`` other than ``"function"`` (e.g.
    ``web_search_preview``) are provider-built-ins: they don't carry a JSON
    schema on our side and the SDK accepts the raw dict, so we pass them
    through untouched. Custom function tools are wrapped as
    :class:`FunctionToolParam` so strict typing applies on the way out.
    """
    if not tools:
        return []
    result: list = []
    for tool in tools:
        tool_type = tool.get("type", "function")
        if tool_type != "function":
            result.append(tool)
        else:
            result.append(
                FunctionToolParam(
                    type="function",
                    name=tool.get("name", ""),
                    description=tool.get("description", ""),
                    parameters=tool.get("parameters"),
                    strict=None,
                )
            )
    return result
