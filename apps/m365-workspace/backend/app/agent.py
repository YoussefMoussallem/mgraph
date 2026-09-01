"""The assistant: a LangChain tool-calling loop over ``m365-langchain-tools``.

Deliberately small. The host-side responsibilities the tools package
documents are all here and nowhere else:

* the tools are constructed **per request** with a ``graph_provider`` built
  from the authenticated caller (``app/routes/agent.py``), never shared;
* ``include_writes`` decides whether the send/upload/move tools exist at all
  for this run — the UI's "allow actions" switch;
* results are the tools' own JSON strings, handed straight back to the model
  and recorded as steps so the UI can show what the assistant did.

No LangGraph, no persistence: a request carries the conversation and gets
back the reply plus the tool steps. Recoverable tool problems (bad
arguments, a wrong id, a 409) come back as text the model can act on —
that is the tools' contract — while infrastructure failures propagate to the
app's error handlers like any other Graph error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from pydantic import ValidationError

#: Upper bound on model turns per request; a runaway loop costs money and
#: time, and eight tool rounds cover every realistic mailbox/files task.
MAX_STEPS = 8

SYSTEM_PROMPT = """\
You are the M365 Workspace assistant for {name} ({email}). Today is {today} (UTC).

You work inside the user's own Microsoft 365 account through tools: Outlook mail, \
attachments, calendar and contacts, and SharePoint sites, document libraries and lists. \
Everything you do runs as the signed-in user with their permissions.

Rules:
- Use the tools to answer; never invent mailbox, calendar or file contents. Use IDs \
exactly as an earlier tool result returned them — they cannot be guessed.
- Prefer reads. Only send mail, reply, forward, create events or respond to invitations \
when the user explicitly asked for exactly that in this conversation. When in doubt, \
create a draft (create_outlook_draft) or ask a short clarifying question.
- If a tool returns an error that explains how to fix the call, fix it and try once more; \
otherwise tell the user what failed and stop.
- Answer concisely in Markdown. Summarize lists (sender, subject, date) instead of \
pasting raw JSON, and mention the folder or site you looked in.
"""


@dataclass(frozen=True)
class ToolStep:
    """One tool invocation, as shown to the user."""

    tool: str
    args: dict[str, Any]
    result: str


@dataclass(frozen=True)
class AgentResult:
    reply: str
    steps: list[ToolStep] = field(default_factory=list)


def _message_text(message: AIMessage) -> str:
    """The text of a model reply, whether ``content`` is a string or blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _tool_error_text(exc: Exception) -> str:
    return f"Error: {exc}. Correct the arguments and call again."


def build_messages(
    history: Sequence[tuple[str, str]], *, name: str, email: str, now: datetime | None = None
) -> list[BaseMessage]:
    """System prompt plus the conversation so far (``(role, content)`` pairs)."""
    today = (now or datetime.now(timezone.utc)).strftime("%A %d %B %Y")
    messages: list[BaseMessage] = [
        SystemMessage(SYSTEM_PROMPT.format(name=name or "the user", email=email or "", today=today))
    ]
    for role, content in history:
        messages.append(HumanMessage(content) if role == "user" else AIMessage(content))
    return messages


async def run_agent(
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    messages: list[BaseMessage],
    *,
    max_steps: int = MAX_STEPS,
) -> AgentResult:
    """Run the model with the tools until it answers or the step budget runs out.

    Each round: the model is called with the conversation, every tool call it
    makes is executed (in order — a later call may depend on an earlier
    result), the results go back as ``ToolMessage``s, and the loop repeats.
    The tools are invoked through ``ainvoke`` so their input schemas are
    validated; a call the model got wrong (bad arguments, a tool that is not
    bound for this run) becomes error text the model can correct.
    """
    by_name = {tool.name: tool for tool in tools}
    model = llm.bind_tools(list(tools)) if tools else llm
    steps: list[ToolStep] = []

    for _ in range(max_steps):
        reply = await model.ainvoke(messages)
        if not isinstance(reply, AIMessage):  # pragma: no cover - defensive
            return AgentResult(reply=str(reply), steps=steps)
        messages.append(reply)
        if not reply.tool_calls:
            return AgentResult(reply=_message_text(reply), steps=steps)

        for call in reply.tool_calls:
            name = call["name"]
            args = dict(call.get("args") or {})
            call_id = call.get("id") or name
            tool = by_name.get(name)
            if tool is None:
                result = (
                    f"Error: unknown tool '{name}'. Only the tools provided in this "
                    "conversation can be called."
                )
            else:
                try:
                    result = str(await tool.ainvoke(args))
                except (ValidationError, ValueError) as exc:
                    result = _tool_error_text(exc)
            steps.append(ToolStep(tool=name, args=args, result=result))
            messages.append(ToolMessage(content=result, tool_call_id=call_id))

    return AgentResult(
        reply=(
            "I stopped after too many steps without reaching an answer. "
            "Try a narrower request, or ask for one thing at a time."
        ),
        steps=steps,
    )
