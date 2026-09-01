# langfuse-client

Python package: **`langfuse_client`** on PyPI-style name **`langfuse-client`** (v0.5.0).

Thin Langfuse initialisation and tracing helpers for AI Labs apps. Safe to import without credentials — tracing is a no-op until `init_client()` runs.

## Install

**From JFrog (apps):** [Installation guide](../docs/installation.md).

**From this repo (development):**

```bash
pip install -e ./sdks/python/langfuse-client
```

Requires Python >= 3.11.

## Usage

```python
from langfuse_client import init_client, get_client, generation, span, flush, shutdown

init_client(
    public_key="pk-...",
    secret_key="sk-...",
    base_url="https://langfuse.example.com",
)
```

See the [package source](src/langfuse_client/) and tests under `tests/` for behaviour (proxy/CA, idempotent init, error isolation).

## Tests

```bash
pip install -e "./sdks/python/langfuse-client[dev]"
pytest sdks/python/langfuse-client/tests -q
```

## Related

- [`llm-provider`](../llm-provider/) — depends on this package for optional LLM trace observations.
- Service context: [SS-03 GenAI proxy](../../../docs/services/SS-03-genai.md).

## Provenance

Canonical home for this package is [`infra-platform-services`](https://github.com/pwc-me-adv-strategyand/infra-platform-services) (`sdks/python/langfuse-client/`). Extracted from Edwin; formerly developed in `genai-python-sdk` (deprecated).
