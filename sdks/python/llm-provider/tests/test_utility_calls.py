"""The non-streaming call shapes: ``generate``, ``generate_chat_completion``,
``generate_image``, ``list_models``.

Pins output assembly, the two self-healing parameter retries (temperature,
image response_format), cached system blocks on the utility path, and usage
normalisation.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx
import openai
import pytest
from llm_provider.adapter import LLMAdapter
from llm_provider.schemas import ChatRequest, Message, SystemBlock

pytestmark = pytest.mark.asyncio


def _status_error(status: int, message: str) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "http://test"))
    return openai.APIStatusError(message, response=response, body=None)


def _adapter_with(namespace):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")
    adapter.client = namespace
    return adapter


# ----------------------------------------------------------------- generate


async def test_generate_assembles_only_output_text():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(type="reasoning"),  # non-message items skipped
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="Hello "),
                    SimpleNamespace(type="refusal", text="IGNORED"),
                    SimpleNamespace(type="output_text", text="world"),
                ],
            ),
        ],
        usage=None,
    )
    captured: dict = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return response

    adapter = _adapter_with(SimpleNamespace(responses=SimpleNamespace(create=_create)))
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")], thinking=True)

    # Cache flags are ignored on this path — plain joined text goes out.
    system = [SystemBlock(text="a", cache=True), SystemBlock(text="b")]
    result = await adapter.generate(req, system)

    assert result == "Hello world"
    assert captured["instructions"] == "ab"
    assert captured["reasoning"] == {"effort": "medium", "summary": "auto"}


# ------------------------------------------------- generate_chat_completion


def _chat_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
    )


async def test_chat_completion_returns_stripped_text():
    async def _create(**kwargs):
        return _chat_response("  A Title  ")

    adapter = _adapter_with(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    )
    result = await adapter.generate_chat_completion(
        model="m", system_prompt="sys", user_content="text"
    )
    assert result == "A Title"


async def test_chat_completion_empty_on_no_choices():
    async def _create(**kwargs):
        return SimpleNamespace(choices=[], usage=None)

    adapter = _adapter_with(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    )
    result = await adapter.generate_chat_completion(
        model="m", system_prompt="sys", user_content="text"
    )
    assert result == ""


async def test_chat_completion_temperature_retry_and_memo():
    # The memo lives on the adapter instance, so a fresh adapter starts clean.
    calls: list[dict] = []

    async def _create(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise _status_error(400, "temperature is deprecated for this model")
        return _chat_response("ok")

    adapter = _adapter_with(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    )

    # First call: 400 on temperature -> dropped -> retried once -> succeeds.
    result = await adapter.generate_chat_completion(
        model="claude-opus-4-7", system_prompt="s", user_content="u"
    )
    assert result == "ok"
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]

    # Model memoised on the adapter: the next call never sends temperature.
    await adapter.generate_chat_completion(
        model="claude-opus-4-7", system_prompt="s", user_content="u"
    )
    assert "temperature" not in calls[2]
    assert len(calls) == 3


async def test_chat_completion_renders_cached_system_blocks():
    captured: dict = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return _chat_response("ok")

    adapter = _adapter_with(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    )
    system = [SystemBlock(text="rules", cache=True), SystemBlock(text="tail")]

    await adapter.generate_chat_completion(
        model="claude-x", system_prompt=system, user_content="u", cache_ttl="5m"
    )
    assert captured["messages"][0]["content"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tail"},
    ]

    # OpenAI model: flags ignored, plain text.
    await adapter.generate_chat_completion(
        model="gpt-4o", system_prompt=system, user_content="u"
    )
    assert captured["messages"][0]["content"] == "rulestail"


async def test_chat_completion_return_usage_normalises_cache_counters():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=9,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        cache_creation_input_tokens=11,
    )

    async def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="t"))], usage=usage
        )

    adapter = _adapter_with(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    )
    result, out = await adapter.generate_chat_completion(
        model="m", system_prompt="s", user_content="u", return_usage=True
    )
    assert result == "t"
    assert out == {
        "input_tokens": 100,
        "output_tokens": 9,
        "cache_read_tokens": 80,
        "cache_write_tokens": 11,
    }


# ------------------------------------------------------------ generate_image


def _image_response(payloads, usage=None):
    data = [SimpleNamespace(b64_json=p) for p in payloads]
    return SimpleNamespace(data=data, usage=usage)


async def test_generate_image_decodes_bytes_and_usage():
    captured: dict = {}
    b64 = base64.b64encode(b"PNGBYTES").decode()

    async def _create(**kwargs):
        captured.update(kwargs)
        return _image_response([b64], usage=SimpleNamespace(input_tokens=12, output_tokens=340))

    adapter = _adapter_with(SimpleNamespace(images=SimpleNamespace(generate=_create)))
    images, usage = await adapter.generate_image(model="gpt-image-1", prompt="a cat")

    assert images == [b"PNGBYTES"]
    assert usage == {"input_tokens": 12, "output_tokens": 340}
    assert captured["response_format"] == "b64_json"
    assert "quality" not in captured  # only forwarded when set


async def test_generate_image_retries_without_response_format():
    calls: list[dict] = []
    b64 = base64.b64encode(b"X").decode()

    async def _create(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise _status_error(400, "Unknown parameter: response_format")
        return _image_response([b64])

    adapter = _adapter_with(SimpleNamespace(images=SimpleNamespace(generate=_create)))
    images, usage = await adapter.generate_image(
        model="gpt-image-1", prompt="p", quality="high"
    )

    assert images == [b"X"]
    assert "response_format" not in calls[1]
    assert calls[1]["quality"] == "high"
    # Flat-priced models report no usage — zeros, caller prices per image.
    assert usage == {"input_tokens": 0, "output_tokens": 0}


async def test_generate_image_skips_undecodable_payloads():
    async def _create(**kwargs):
        return _image_response(["!!!not-base64!!!", base64.b64encode(b"OK").decode(), None])

    adapter = _adapter_with(SimpleNamespace(images=SimpleNamespace(generate=_create)))
    images, _ = await adapter.generate_image(model="m", prompt="p")
    assert images == [b"OK"]


# -------------------------------------------------------------- list_models


async def test_list_models_returns_trimmed_records():
    async def _list():
        return SimpleNamespace(
            data=[SimpleNamespace(id="m1", owned_by="org", noisy_field="x")]
        )

    adapter = _adapter_with(SimpleNamespace(models=SimpleNamespace(list=_list)))
    assert await adapter.list_models() == [{"id": "m1", "owned_by": "org"}]
