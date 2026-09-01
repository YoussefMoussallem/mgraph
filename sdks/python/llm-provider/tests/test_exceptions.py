"""Exception classification and the adapter's translation boundary.

Application code catches ``llm_provider.exceptions`` types only — the
adapter must never leak ``openai.*`` exceptions.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest
from llm_provider.adapter import LLMAdapter
from llm_provider.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderServerError,
    classify_status_error,
)
from llm_provider.schemas import ChatRequest, Message


def test_classify_maps_every_status_family():
    cases = {
        401: ProviderAuthError,
        403: ProviderAuthError,
        429: ProviderRateLimitError,
        404: ProviderNotFoundError,
        400: ProviderInvalidRequestError,
        500: ProviderServerError,
        503: ProviderServerError,
    }
    for status, exc_type in cases.items():
        err = classify_status_error(status, "msg")
        assert type(err) is exc_type, status
        assert err.status_code == status

    # Unmapped codes fall back to the base class, never raise KeyError.
    fallback = classify_status_error(418, "teapot")
    assert type(fallback) is ProviderError


def _status_error(status: int) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "http://test"))
    return openai.APIStatusError("provider said no", response=response, body=None)


def _adapter_raising(exc):
    adapter = LLMAdapter(api_key="test", base_url="http://localhost/v1")

    async def _create(**kwargs):
        raise exc

    adapter.client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    return adapter


@pytest.mark.asyncio
async def test_stream_translates_status_errors():
    adapter = _adapter_raising(_status_error(429))
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])
    with pytest.raises(ProviderRateLimitError) as info:
        _ = [ev async for ev in adapter.stream(req, "sys")]
    # Original SDK error preserved as the cause for debugging.
    assert isinstance(info.value.__cause__, openai.APIStatusError)


@pytest.mark.asyncio
async def test_stream_translates_timeouts_to_connection_error():
    adapter = _adapter_raising(openai.APITimeoutError(request=httpx.Request("POST", "http://t")))
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])
    with pytest.raises(ProviderConnectionError, match="timed out"):
        _ = [ev async for ev in adapter.stream(req, "sys")]


@pytest.mark.asyncio
async def test_generate_translates_connection_errors():
    adapter = _adapter_raising(
        openai.APIConnectionError(request=httpx.Request("POST", "http://t"))
    )
    req = ChatRequest(model="m", messages=[Message(role="user", content="q")])
    with pytest.raises(ProviderConnectionError):
        await adapter.generate(req, "sys")
