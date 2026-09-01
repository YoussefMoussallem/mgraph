"""Langfuse observability pass-through.

Request identity (user_id / session_id / trace_metadata / trace_tags) must
reach the tracing helpers on every call shape, and the final observation
update must carry the tool-call summary, the cache token counters, and —
when the app injects a ``cost_fn`` pricer — the cost breakdown.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import llm_provider.adapter.chat_completions as adapter_chat
import llm_provider.adapter.core as adapter_core
import llm_provider.adapter.images as adapter_images
import llm_provider.adapter.responses as adapter_responses
import pytest
from llm_provider.adapter import LLMAdapter
from llm_provider.schemas import ChatRequest, Message, SystemBlock

pytestmark = pytest.mark.asyncio

IDENTITY = {
    "user_id": "user-42",
    "session_id": "sess-7",
    "metadata": {"app": "edwin"},
    "tags": ["prod"],
}


class _RecordingCtx:
    def __init__(self):
        self.updates: list[dict] = []

    def __enter__(self):
        return SimpleNamespace(update=lambda **kw: self.updates.append(kw))

    def __exit__(self, *a):
        return False


def _capture(store):
    """Stand-in for langfuse generation()/span() recording attrs + updates."""

    def _factory(name, model, input_data=None, **attrs):
        store["name"] = name
        store["attrs"] = attrs
        ctx = _RecordingCtx()
        store["ctx"] = ctx
        return ctx

    return _factory


def _identified_request(**extra):
    return ChatRequest(
        model="m",
        messages=[Message(role="user", content="q")],
        user_id="user-42",
        session_id="sess-7",
        trace_metadata={"app": "edwin"},
        trace_tags=["prod"],
        **extra,
    )


def _responses_adapter(events, cost_fn=None):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1", cost_fn=cost_fn)

    async def _create(**kwargs):
        async def _gen():
            for e in events:
                yield e

        return _gen()

    adapter.client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    return adapter


def _chat_adapter(chunks, cost_fn=None):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1", cost_fn=cost_fn)

    async def _create(**kwargs):
        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    adapter.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    return adapter


async def test_responses_stream_passes_identity_and_records_tools(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=4),
        cache_creation_input_tokens=2,
    )
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call", id="i1", call_id="call_1", name="MakeSlide"
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done", item_id="i1", arguments="{}"
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=usage, output=[]),
        ),
    ]
    adapter = _responses_adapter(events)

    _ = [ev async for ev in adapter.stream(_identified_request(), "sys")]

    assert store["attrs"] == IDENTITY
    update = store["ctx"].updates[-1]
    assert update["metadata"]["tool_calls"] == [{"call_id": "call_1", "name": "MakeSlide"}]
    assert update["usage_details"]["cache_read_input_tokens"] == 4
    assert update["usage_details"]["cache_creation_input_tokens"] == 2


async def test_responses_stream_without_identity_passes_no_attrs(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))
    adapter = _responses_adapter([])
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])

    _ = [ev async for ev in adapter.stream(req, "sys")]

    assert store["attrs"] == {}
    # No tool calls -> no metadata key on the final update.
    assert "metadata" not in store["ctx"].updates[-1]


async def test_chat_stream_passes_identity_and_records_tools(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_chat, "langfuse_generation", _capture(store))
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        cache_creation_input_tokens=15,
    )
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="c1",
                                function=SimpleNamespace(name="ListSlides", arguments="{}"),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(choices=[], usage=usage),
    ]
    adapter = _chat_adapter(chunks)
    system = [SystemBlock(text="S", cache=True), SystemBlock(text="t")]

    _ = [ev async for ev in adapter.stream(_identified_request(), system)]

    assert store["attrs"] == IDENTITY
    update = store["ctx"].updates[-1]
    assert update["metadata"]["tool_calls"] == [{"call_id": "c1", "name": "ListSlides"}]
    assert update["usage_details"]["cache_read_input_tokens"] == 80
    assert update["usage_details"]["cache_creation_input_tokens"] == 15


async def test_complete_passes_identity_to_span(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_core, "langfuse_span", _capture(store))
    adapter = _responses_adapter([])

    _ = [ev async for ev in adapter.complete(_identified_request(), "sys")]

    assert store["attrs"] == IDENTITY


async def test_generate_passes_identity(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))
    adapter = _responses_adapter([])

    async def _create(**kwargs):
        return SimpleNamespace(output=[], usage=None)

    adapter.client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    await adapter.generate(_identified_request(), "sys")

    assert store["attrs"] == IDENTITY


async def test_generate_chat_completion_identity_passthrough(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_chat, "langfuse_generation", _capture(store))
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="t"))], usage=None
        )

    adapter.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    await adapter.generate_chat_completion(
        model="m",
        system_prompt="s",
        user_content="u",
        user_id="user-42",
        session_id="sess-7",
        trace_metadata={"app": "edwin"},
        trace_tags=["prod"],
    )
    assert store["attrs"] == IDENTITY


async def test_generate_image_identity_passthrough(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_images, "langfuse_generation", _capture(store))
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        return SimpleNamespace(data=[], usage=None)

    adapter.client = SimpleNamespace(images=SimpleNamespace(generate=_create))
    await adapter.generate_image(
        model="m",
        prompt="p",
        user_id="user-42",
        session_id="sess-7",
        trace_metadata={"app": "edwin"},
        trace_tags=["prod"],
    )
    assert store["attrs"] == IDENTITY


def _pricer(model, usage):
    # $/token: input 2e-6, output 1e-5, cache reads at 10% of input.
    return {
        "input": usage.get("input", 0) * 2e-6,
        "output": usage.get("output", 0) * 1e-5,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) * 2e-7,
    }


async def test_responses_stream_reports_cost_details(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=100,
        input_tokens_details=SimpleNamespace(cached_tokens=400),
        cache_creation_input_tokens=0,
    )
    events = [
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=usage, output=[]),
        )
    ]
    adapter = _responses_adapter(events, cost_fn=_pricer)

    _ = [ev async for ev in adapter.stream(_identified_request(), "sys")]

    costs = store["ctx"].updates[-1]["cost_details"]
    assert costs["input"] == pytest.approx(1000 * 2e-6)
    assert costs["output"] == pytest.approx(100 * 1e-5)
    assert costs["cache_read_input_tokens"] == pytest.approx(400 * 2e-7)


async def test_chat_stream_reports_cost_details(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_chat, "langfuse_generation", _capture(store))
    usage = SimpleNamespace(prompt_tokens=500, completion_tokens=50)
    chunks = [SimpleNamespace(choices=[], usage=usage)]
    adapter = _chat_adapter(chunks, cost_fn=_pricer)
    system = [SystemBlock(text="S", cache=True)]

    _ = [ev async for ev in adapter.stream(_identified_request(), system)]

    costs = store["ctx"].updates[-1]["cost_details"]
    assert costs["input"] == pytest.approx(500 * 2e-6)
    assert costs["output"] == pytest.approx(50 * 1e-5)


async def test_generate_chat_completion_reports_cost_details(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_chat, "langfuse_generation", _capture(store))
    adapter = LLMAdapter(
        api_key="test", base_url="http://localhost/v1", cost_fn=_pricer
    )

    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="t"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
        )

    adapter.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    await adapter.generate_chat_completion(model="m", system_prompt="s", user_content="u")

    costs = store["ctx"].updates[-1]["cost_details"]
    assert costs["input"] == pytest.approx(10 * 2e-6)
    assert costs["output"] == pytest.approx(2 * 1e-5)


async def test_generate_image_reports_flat_cost_from_image_count(monkeypatch):
    """Flat-priced image models (DALL·E) report zero token usage; the
    ``images`` custom usage type carries the count so the pricer can bill
    per image."""
    store: dict = {}
    monkeypatch.setattr(adapter_images, "langfuse_generation", _capture(store))

    def flat_pricer(model, usage):
        return {"images": usage["images"] * 0.04}

    adapter = LLMAdapter(
        api_key="test", base_url="http://localhost/v1", cost_fn=flat_pricer
    )
    b64 = base64.b64encode(b"png").decode()

    async def _create(**kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=b64), SimpleNamespace(b64_json=b64)],
            usage=None,
        )

    adapter.client = SimpleNamespace(images=SimpleNamespace(generate=_create))
    await adapter.generate_image(model="dall-e-3", prompt="p", n=2)

    update = store["ctx"].updates[-1]
    assert update["usage_details"]["images"] == 2
    assert update["cost_details"]["images"] == pytest.approx(0.08)


async def test_cost_fn_failure_never_breaks_the_call(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))

    def broken(model, usage):
        raise RuntimeError("pricing table down")

    adapter = _responses_adapter([], cost_fn=broken)

    events = [ev async for ev in adapter.stream(_identified_request(), "sys")]

    assert events[-1].event == "done"
    assert "cost_details" not in store["ctx"].updates[-1]


async def test_no_cost_fn_omits_cost_details(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(adapter_responses, "langfuse_generation", _capture(store))
    adapter = _responses_adapter([])

    _ = [ev async for ev in adapter.stream(_identified_request(), "sys")]

    assert "cost_details" not in store["ctx"].updates[-1]
