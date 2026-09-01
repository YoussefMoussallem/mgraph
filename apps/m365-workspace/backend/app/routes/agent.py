"""Assistant routes — ``m365-langchain-tools`` bound to the caller, per request.

``/v1/agent/chat`` is the whole host integration the tools package
describes: build a ``graph_provider`` from trusted request context (the
caller's own Graph client from ``app/graph.py``), construct fresh tools with
it, decide ``include_writes`` from the request, run the loop in
``app/agent.py``. The model never sees, and can never choose, whose
mailbox it is in.

``/v1/agent/tools`` is the palette the UI shows; ``/v1/agent/status`` tells
it whether an LLM is configured at all.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.language_models import BaseChatModel
from m365_langchain_tools import m365_tools
from msgraph import GraphServiceClient
from pydantic import BaseModel, Field

from app.agent import build_messages, run_agent
from app.config import get_settings
from app.dependencies import CurrentUser, get_current_user
from app.graph import get_graph

router = APIRouter(prefix="/v1/agent", tags=["agent"])

Graph = Annotated[GraphServiceClient, Depends(get_graph)]
User = Annotated[CurrentUser, Depends(get_current_user)]

#: ``app.state`` attribute a test (or an embedding host) can set to a
#: zero-argument callable returning a ``BaseChatModel``, replacing the
#: configured OpenAI-compatible model.
LLM_FACTORY_KEY = "llm_factory"


# ── Models ───────────────────────────────────────────────────────────


class ToolInfo(BaseModel):
    name: str
    description: str
    write: bool


class AgentStatus(BaseModel):
    configured: bool
    model: str | None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    include_writes: bool = False


class ToolStepOut(BaseModel):
    tool: str
    args: dict[str, Any]
    result: str


class ChatResponse(BaseModel):
    reply: str
    steps: list[ToolStepOut]
    include_writes: bool


# ── LLM ──────────────────────────────────────────────────────────────


def get_llm(request: Request) -> BaseChatModel:
    """The chat model for this request: an override on ``app.state``, else OpenAI-compatible."""
    factory = getattr(request.app.state, LLM_FACTORY_KEY, None)
    if factory is not None:
        return factory()

    agent = get_settings().agent
    if not agent.configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "The assistant is not configured: set OPENAI_API_KEY and AGENT_MODEL "
                "(and OPENAI_BASE_URL for a proxy) in the backend environment."
            ),
        )
    # Imported here so the package is only loaded when the assistant is used.
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=agent.model,
        api_key=agent.api_key.get_secret_value(),
        base_url=agent.base_url or None,
    )


LLM = Annotated[BaseChatModel, Depends(get_llm)]


async def _no_graph() -> GraphServiceClient:  # pragma: no cover - never called
    raise RuntimeError("palette tools are never invoked")


# ── Routes ───────────────────────────────────────────────────────────


@router.get("/status", response_model=AgentStatus)
async def status(request: Request, user: User) -> AgentStatus:
    agent = get_settings().agent
    overridden = getattr(request.app.state, LLM_FACTORY_KEY, None) is not None
    return AgentStatus(
        configured=overridden or agent.configured,
        model=agent.model or None if not overridden else "override",
    )


@router.get("/tools", response_model=list[ToolInfo])
async def tools(user: User, include_writes: bool = True) -> list[ToolInfo]:
    """The tool palette: every tool the assistant can be given, flagged read/write."""
    read_names = {tool.name for tool in m365_tools(_no_graph, include_writes=False)}
    return [
        ToolInfo(name=tool.name, description=tool.description, write=tool.name not in read_names)
        for tool in m365_tools(_no_graph, include_writes=include_writes)
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, graph: Graph, user: User, llm: LLM) -> ChatResponse:
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="The last message must be from the user")

    async def graph_provider() -> GraphServiceClient:
        # Trusted context only: the Graph client already built for this
        # caller by app.graph.get_graph. Nothing from the model reaches it.
        return graph

    agent_tools = m365_tools(graph_provider, include_writes=body.include_writes)
    messages = build_messages(
        [(message.role, message.content) for message in body.messages],
        name=user.display_name,
        email=user.email,
    )
    result = await run_agent(llm, agent_tools, messages)
    return ChatResponse(
        reply=result.reply,
        steps=[ToolStepOut(tool=s.tool, args=s.args, result=s.result) for s in result.steps],
        include_writes=body.include_writes,
    )
