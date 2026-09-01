"""The assistant: palette, status, and the tool-calling loop with a scripted model.

The LLM is the only thing stubbed. Tool calls the scripted model makes run
through the real ``m365-langchain-tools`` classes against the mock Graph,
so a step's ``result`` is what the model would actually have seen.
"""

from __future__ import annotations

import json
from typing import Any

from app.routes.agent import LLM_FACTORY_KEY
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

BASE = "/api/v1/agent"


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed sequence of replies and records what it was bound to."""

    responses: list[AIMessage]
    bound_tools: list[str] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        self.bound_tools = [tool.name for tool in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def _tool_call(name: str, args: dict, call_id: str = "call-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _install(app, responses: list[AIMessage]) -> ScriptedChatModel:
    model = ScriptedChatModel(responses=responses)
    setattr(app.state, LLM_FACTORY_KEY, lambda: model)
    return model


# ── Palette and status ───────────────────────────────────────────────


def test_palette_lists_every_tool_with_a_write_flag(api: TestClient) -> None:
    res = api.get(f"{BASE}/tools")
    assert res.status_code == 200
    tools = res.json()
    assert len(tools) == 24
    assert sum(t["write"] for t in tools) == 11
    assert all(len(t["description"]) > 80 for t in tools)

    res = api.get(f"{BASE}/tools", params={"include_writes": "false"})
    assert len(res.json()) == 13
    assert not any(t["write"] for t in res.json())


def test_status_reports_unconfigured_by_default(api: TestClient) -> None:
    res = api.get(f"{BASE}/status")
    assert res.status_code == 200
    assert res.json() == {"configured": False, "model": None}


def test_chat_without_an_llm_is_a_503(api: TestClient) -> None:
    res = api.post(f"{BASE}/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 503
    assert res.json()["code"] == "service_unavailable"
    assert "OPENAI_API_KEY" in res.json()["detail"]


# ── The loop ─────────────────────────────────────────────────────────


def test_chat_runs_a_tool_and_returns_the_steps(api: TestClient, workspace_app) -> None:
    model = _install(
        workspace_app,
        [
            _tool_call("list_outlook_messages", {"top": 3}),
            AIMessage(content="You have one message from Ada: 'Hello'."),
        ],
    )
    res = api.post(
        f"{BASE}/chat",
        json={"messages": [{"role": "user", "content": "What's new in my inbox?"}]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reply"].startswith("You have one message")
    assert body["include_writes"] is False
    assert len(body["steps"]) == 1
    step = body["steps"][0]
    assert step["tool"] == "list_outlook_messages"
    assert step["args"] == {"top": 3}
    assert json.loads(step["result"])["count"] == 1

    # Read-only run: 13 tools bound, and the system prompt names the caller.
    assert len(model.bound_tools) == 13
    assert "send_outlook_message" not in model.bound_tools
    first_prompt = model.seen[0][0].content
    assert "Test User" in first_prompt and "user@example.com" in first_prompt
    # The second model turn saw the tool result.
    assert model.seen[1][-1].type == "tool"


def test_writes_are_only_bound_when_asked(api: TestClient, workspace_app) -> None:
    model = _install(
        workspace_app,
        [
            _tool_call("send_outlook_message", {"to": ["b@x.com"], "subject": "S", "body": "B"}),
            AIMessage(content="Sent."),
        ],
    )
    res = api.post(
        f"{BASE}/chat",
        json={
            "messages": [{"role": "user", "content": "Send B to b@x.com with subject S"}],
            "include_writes": True,
        },
    )
    assert res.status_code == 200, res.text
    assert len(model.bound_tools) == 24
    assert json.loads(res.json()["steps"][0]["result"])["sent"] is True


def test_a_tool_that_is_not_bound_becomes_error_text(api: TestClient, workspace_app) -> None:
    _install(
        workspace_app,
        [
            _tool_call("send_outlook_message", {"to": ["b@x.com"], "subject": "S", "body": "B"}),
            AIMessage(content="I can't send mail in this mode."),
        ],
    )
    res = api.post(
        f"{BASE}/chat",
        json={"messages": [{"role": "user", "content": "send it"}], "include_writes": False},
    )
    assert res.status_code == 200
    step = res.json()["steps"][0]
    assert step["result"].startswith("Error: unknown tool 'send_outlook_message'")


def test_bad_arguments_become_error_text_not_a_500(api: TestClient, workspace_app) -> None:
    _install(
        workspace_app,
        [
            _tool_call("get_outlook_message", {"message_id": ""}),
            AIMessage(content="I need a message id first."),
        ],
    )
    res = api.post(f"{BASE}/chat", json={"messages": [{"role": "user", "content": "open it"}]})
    assert res.status_code == 200
    assert res.json()["steps"][0]["result"].startswith("Error:")


def test_last_message_must_be_from_the_user(api: TestClient, workspace_app) -> None:
    _install(workspace_app, [])
    res = api.post(
        f"{BASE}/chat",
        json={"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
    )
    assert res.status_code == 422


def test_step_budget_ends_a_runaway_loop(api: TestClient, workspace_app) -> None:
    _install(workspace_app, [_tool_call("list_outlook_folders", {}, f"call-{i}") for i in range(8)])
    res = api.post(f"{BASE}/chat", json={"messages": [{"role": "user", "content": "loop"}]})
    assert res.status_code == 200
    assert "too many steps" in res.json()["reply"]
    assert len(res.json()["steps"]) == 8
