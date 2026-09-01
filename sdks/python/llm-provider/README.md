# llm-provider

Python package: **`llm_provider`** on PyPI-style name **`llm-provider`** (v0.4.0).

Async LLM adapter on the OpenAI Python SDK: streaming (Responses and Chat Completions), tool use, vision, image generation, prompt caching, normalized usage, and a provider-agnostic exception hierarchy. Works against any OpenAI-compatible endpoint (e.g. LiteLLM → Bedrock, Azure OpenAI, Gemini).

## Install

**From JFrog (apps):** [Installation guide](../docs/installation.md) in the Python docs.

**From this repo (development):**

```bash
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
```

Requires Python >= 3.11.

## Quickstart

```python
from llm_provider import ChatRequest, LLMAdapter, Message

adapter = LLMAdapter(api_key="sk-...", base_url="https://your-proxy/v1")
request = ChatRequest(
    model="claude-sonnet-5",
    messages=[Message(role="user", content="Say hello.")],
    max_output_tokens=8192,
)

text = await adapter.generate(request, system_prompt="You are helpful.")
```

Optional tracing: `from langfuse_client import init_client` then call `init_client(...)` before LLM calls.

## Tests

```bash
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
pytest sdks/python/llm-provider/tests sdks/python/langfuse-client/tests -q
```

## Related

- [`langfuse-client`](../langfuse-client/) — tracing helpers used by the adapter.
- Service context: [SS-03 GenAI proxy](../../../docs/services/SS-03-genai.md).

## Provenance

Canonical home for this package is [`infra-platform-services`](https://github.com/pwc-me-adv-strategyand/infra-platform-services) (`sdks/python/llm-provider/`). Extracted from Edwin; formerly developed in `genai-python-sdk` (deprecated).
