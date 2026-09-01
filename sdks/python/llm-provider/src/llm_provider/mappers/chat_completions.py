"""Wire mappers for Chat Completions (``chat.completions.create``).

Fills ``messages`` — including the ``role: "system"`` entry — and the
nested tool definitions. This is the one dialect that carries
``cache_control`` breakpoints (the only path where LiteLLM forwards them
to the backend), so the cache-rendering machinery lives here too.
"""

import logging

from llm_provider.schemas import Message, SystemBlock, system_text

logger = logging.getLogger(__name__)


def cache_control(ttl: str) -> dict:
    """Build the ``cache_control`` ephemeral breakpoint for the given tier.

    The **bare** ``{"type": "ephemeral"}`` (default 5-minute TTL) is the form
    Bedrock (and Anthropic) accept everywhere. The explicit ``ttl`` field is
    Anthropic's *extended-cache beta* (1-hour) and is NOT understood by
    Bedrock — sending it makes the proxy ignore the whole ``cache_control``, so
    nothing caches. Only emit ``ttl`` for the explicit ``"1h"`` extended tier;
    every other value (``"5m"``/default) uses the bare form.
    """
    cc: dict = {"type": "ephemeral"}
    if ttl == "1h":
        cc["ttl"] = "1h"
    return cc


def render_cache_blocks(blocks: list[SystemBlock], ttl: str) -> list[dict]:
    """Render :class:`SystemBlock`\\ s as Chat Completions content blocks,
    attaching a ``cache_control`` ephemeral breakpoint to each block flagged
    ``cache=True``.

    Chat-only by construction: the adapter routes every caching request to
    ``chat.completions`` (the one path where LiteLLM forwards ``cache_control``
    to the backend), so there is no Responses variant of this shape.
    """
    out: list[dict] = []
    for b in blocks:
        block: dict = {"type": "text", "text": b.text}
        if b.cache:
            block["cache_control"] = cache_control(ttl)
        out.append(block)
    return out


def build_system_message(
    blocks: list[SystemBlock], *, cache_ttl: str | None = None
) -> dict:
    """Render the ``role: "system"`` message.

    With ``cache_ttl`` set and at least one cache-flagged block, content is
    rendered as ``cache_control`` content blocks
    (:func:`render_cache_blocks`); otherwise the blocks join to plain text —
    the shape every backend accepts, and the one OpenAI (which rejects
    ``cache_control`` and caches automatically by prefix) requires. The
    caller owns the policy — pass ``None`` for models where ``cache_control``
    doesn't apply.
    """
    content: str | list[dict]
    if cache_ttl and any(b.cache for b in blocks):
        content = render_cache_blocks(blocks, cache_ttl)
    else:
        content = system_text(blocks)
    return {"role": "system", "content": content}


def _attach_message_cache(items: list[dict], ttl: str) -> None:
    """Attach a ``cache_control`` breakpoint to the last message that can carry
    one, so the whole request prefix above it caches at ``ttl``.

    Caching is prefix-based: a breakpoint on the final message caches
    everything before it (including the system message the adapter prepends —
    cached at its own, longer tier; longest-TTL-first ordering is what
    Anthropic requires when tiers are mixed). On the next turn that message
    sits mid-history, so the request reads the cached prefix and writes a
    fresh breakpoint at its own last message — the breakpoint "moves" with
    the conversation, giving incremental history caching across the short
    ``ttl`` window (and across the loop's tool-use rounds within a turn).

    The provider wants ``cache_control`` on a content *block*, so a plain
    string ``content`` is promoted to a one-block list. No-op if no message
    can carry it (e.g. a trailing tool-call turn whose ``content`` is
    ``None``).
    """
    for msg in reversed(items):
        content = msg.get("content")
        if isinstance(content, str):
            if not content:
                continue
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": cache_control(ttl)}
            ]
        elif isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = cache_control(ttl)
        else:
            continue
        return


def build_messages(
    messages: list[Message], *, cache_ttl: str | None = None
) -> list[dict]:
    """Translate a normalised :class:`Message` list into Chat Completions messages.

    The counterpart of :func:`llm_provider.mappers.responses.build_input`.
    Two differences matter for tool use:

    * an assistant turn's text and its tool calls live in a **single** message
      (``content`` + ``tool_calls``), not separate items as on Responses; and
    * a tool result is a ``role: "tool"`` message keyed by ``tool_call_id``
      (vs a ``function_call_output`` item).

    Tool-call ids pass through verbatim, so each ``tool`` message pairs with the
    assistant ``tool_calls[].id`` that produced it — the provider matches them
    by that id regardless of which API minted it.

    ``cache_ttl``, when set, attaches a ``cache_control`` breakpoint to the
    last message so the conversation prefix caches at that tier (see
    :func:`_attach_message_cache`). The caller owns the policy — pass ``None``
    for models that don't support ``cache_control``.
    """
    items: list[dict] = []
    for msg in messages:
        if msg.role == "user":
            if msg.images:
                parts: list[dict] = [{"type": "text", "text": msg.content or ""}]
                for img in msg.images:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{img.mime_type};base64,{img.base64}"},
                        }
                    )
                items.append({"role": "user", "content": parts})
            else:
                items.append({"role": "user", "content": msg.content or ""})

        elif msg.role == "assistant":
            item: dict = {"role": "assistant"}
            if msg.tool_calls:
                # A tool-calls-only turn has no text: send ``content: null`` (not
                # "") so Bedrock doesn't reject an empty assistant turn.
                item["content"] = msg.content or None
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            else:
                item["content"] = msg.content or ""
            items.append(item)

        elif msg.role == "tool":
            items.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                }
            )

    if cache_ttl:
        _attach_message_cache(items, cache_ttl)
    return items


def build_tools(tools: list[dict] | None) -> list[dict]:
    """Translate function tool definitions into Chat Completions tool shape.

    The counterpart of :func:`llm_provider.mappers.responses.build_tools`:
    each function tool nests its schema under a ``"function"`` key (vs flat on
    Responses). Provider built-ins (e.g. ``web_search_preview``) aren't
    supported on this endpoint and are skipped — the agent reaches web search
    through a separate function tool on a secondary model, so nothing on this
    path relies on a hosted tool. A missing ``parameters`` becomes an empty
    object schema, which the API requires.
    """
    if not tools:
        return []
    result: list[dict] = []
    for tool in tools:
        if tool.get("type", "function") != "function":
            # Dropping a hosted tool silently would look to the caller like
            # the model simply never used it — make the loss visible.
            logger.warning(
                "Dropping non-function tool %r: hosted tools are not "
                "supported on the chat.completions path",
                tool.get("type"),
            )
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return result
