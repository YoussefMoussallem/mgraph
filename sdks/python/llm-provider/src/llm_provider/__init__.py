"""llm_provider — provider-agnostic LLM client built on the OpenAI Python SDK.

Wraps :class:`openai.AsyncOpenAI` behind a small normalized schema
(:class:`Message`, :class:`ChatRequest`, :class:`SystemBlock`,
:class:`StreamEvent`) so application code never touches SDK-specific types.
Main-loop streaming uses the Responses API; cache-flagged requests and
utility callers use chat completions.
"""

from llm_provider.adapter import LLMAdapter
from llm_provider.schemas import (
    ChatRequest,
    ImageData,
    Message,
    StreamEvent,
    SystemBlock,
    ToolCallData,
)

__all__ = [
    "ChatRequest",
    "ImageData",
    "LLMAdapter",
    "Message",
    "StreamEvent",
    "SystemBlock",
    "ToolCallData",
]
