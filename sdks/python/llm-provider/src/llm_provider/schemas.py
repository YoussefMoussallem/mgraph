"""Provider-agnostic request / message / event schemas.

These types form the boundary between application code and the OpenAI SDK.
Callers construct :class:`ChatRequest` from their own domain objects, and
the adapter translates them into SDK-specific types via
:mod:`llm_provider.mappers`. Keeping this translation layer thin lets us
swap the underlying client without touching the rest of the codebase.
"""

from pydantic import BaseModel


class SystemBlock(BaseModel):
    """One segment of a structured system prompt.

    ``cache=True`` places a prompt-cache breakpoint AFTER this block: the
    provider caches everything up to and including it, keyed on the exact
    bytes. Flag the block that ends a stable prefix (static rules, tool
    docs) and keep volatile content (dates, per-request context) in later,
    unflagged blocks — any byte churn before a breakpoint turns every call
    into a cache miss.

    Adapter methods accept ``str | list[SystemBlock]`` for the system
    prompt; a plain ``str`` is equivalent to one unflagged block and is
    never cached.
    """

    text: str
    cache: bool = False


def system_text(blocks: list[SystemBlock]) -> str:
    """The system prompt as plain text — blocks joined, cache flags ignored.

    Used wherever ``cache_control`` can't apply: the Responses path (the
    ``instructions`` kwarg), models that cache automatically (OpenAI), and
    trace payloads.
    """
    return "".join(b.text for b in blocks)


class ImageData(BaseModel):
    """Inline image payload attached to a user message.

    Stored as base64 rather than a URL so message history is self-contained
    and replayable without depending on external storage still being
    reachable.
    """

    mime_type: str
    base64: str


class ToolCallData(BaseModel):
    """A single tool/function call produced by the assistant.

    ``arguments`` is kept as a raw JSON string rather than a dict because
    the LLM streams them character-by-character; parsing is deferred to the
    caller so partial arguments can be surfaced to the UI progressively.
    """

    id: str
    name: str
    arguments: str


class Message(BaseModel):
    """One turn of a chat conversation.

    Shape intentionally matches the three OpenAI roles we support. ``content``
    is optional because assistant turns can consist solely of tool calls, and
    tool turns carry the call id they're answering in ``tool_call_id``.
    """

    role: str  # "user" | "assistant" | "tool"
    content: str | None = None
    images: list[ImageData] | None = None
    tool_calls: list[ToolCallData] | None = None
    tool_call_id: str | None = None  # for role="tool"


class ChatRequest(BaseModel):
    """Input envelope for :meth:`LLMAdapter.stream` / ``complete`` / ``generate``.

    Attributes:
        model: Provider model id (e.g. ``"claude-opus-4-7"``).
        messages: Conversation turns, oldest first.
        tools: Optional function/tool definitions. Non-``function`` types
            (e.g. ``web_search_preview``) are passed through to the provider
            untouched; see :func:`llm_provider.mappers.responses.build_tools`.
        thinking: Enable reasoning/thinking output. When ``True`` the adapter
            requests reasoning summaries and emits ``thinking_delta`` events.
        cache_ttl: Ephemeral cache tier for :class:`SystemBlock` breakpoints —
            ``"5m"`` (default; the bare form every backend accepts, incl.
            Bedrock) or ``"1h"`` (Anthropic's extended-cache beta; Bedrock
            ignores the whole breakpoint if it sees the explicit ttl field).
            Only takes effect when the system prompt contains cache-flagged
            blocks and the model supports ``cache_control``.
        message_cache_ttl: When set, the Chat Completions path also caches the
            conversation — it attaches a ``cache_control`` breakpoint at this
            TTL to the final message, so the system→history prefix caches and
            "moves" forward each turn. ``None`` (default) caches only the
            system prefix.
        max_output_tokens: Cap on tokens the model may generate this turn. When
            ``None`` the provider's (often low) default applies — which silently
            truncates large tool calls (e.g. writing a big HTML file) mid-
            arguments. Callers that may emit large output should set this to the
            model's real capacity.
        user_id: Observability identity — propagated to the Langfuse trace
            (per-user cost/usage aggregation keys on it). Never sent to the
            LLM provider.
        session_id: Observability identity — groups the traces of one
            conversation/session in Langfuse. Never sent to the provider.
        trace_metadata: Extra key-value dimensions for the Langfuse trace
            (small correlating identifiers, not payloads). Never sent to the
            provider.
        trace_tags: Langfuse tags for filtering traces. Never sent to the
            provider.
    """

    model: str
    messages: list[Message]
    tools: list[dict] | None = None
    thinking: bool = False
    cache_ttl: str = "5m"
    message_cache_ttl: str | None = None
    max_output_tokens: int | None = None
    user_id: str | None = None
    session_id: str | None = None
    trace_metadata: dict | None = None
    trace_tags: list[str] | None = None


class StreamEvent:
    """Normalised streaming event emitted by :meth:`LLMAdapter.stream`.

    Provider-specific SDK events are mapped into a small stable set of
    names (``text_delta``, ``thinking_delta``, ``tool_call_start``,
    ``tool_call_delta``, ``tool_call_done``, ``web_search_*``, ``error``,
    ``done``). Consumers should treat unknown event names as no-ops so new
    event types can be added without breaking them.

    Not a Pydantic model on purpose: events are produced in a hot loop and
    :class:`BaseModel` validation is measurable overhead there.
    """

    def __init__(self, event: str, data: dict):
        self.event = event
        self.data = data
