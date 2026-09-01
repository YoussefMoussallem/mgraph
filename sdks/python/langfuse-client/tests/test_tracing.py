"""``generation`` / ``span`` helper contract.

Two invariants callers rely on: the helpers return ``None`` whenever tracing
is off (guarded with ``if ctx:`` everywhere), and they NEVER raise — a
Langfuse/OTel failure must not take down the LLM call being traced.
"""

from __future__ import annotations

from types import SimpleNamespace

import langfuse_client.client as client_mod
import langfuse_client.tracing as tracing_mod
import pytest
from langfuse_client import generation, span


@pytest.fixture
def no_client(monkeypatch):
    monkeypatch.setattr(client_mod, "_client", None)


@pytest.fixture
def observed(monkeypatch):
    """Fake initialised client; captures start_as_current_observation kwargs."""
    calls: dict = {}

    def _start(**kwargs):
        calls.update(kwargs)
        return "CTX"

    monkeypatch.setattr(
        client_mod,
        "_client",
        SimpleNamespace(start_as_current_observation=_start),
    )
    return calls


def test_helpers_noop_without_client(no_client):
    assert generation("g", "model-x") is None
    assert span("s", "model-x") is None


def test_generation_passes_model_first_class(observed):
    assert generation("g", "model-x", {"a": 1}) == "CTX"
    assert observed == {
        "as_type": "generation",
        "name": "g",
        "model": "model-x",
        "input": {"a": 1},
    }


def test_span_folds_model_into_input(observed):
    # The span observation type has no first-class model field.
    assert span("s", "model-x", {"a": 1}) == "CTX"
    assert observed == {
        "as_type": "span",
        "name": "s",
        "input": {"model": "model-x", "a": 1},
    }


def test_helpers_swallow_langfuse_errors(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("otel exploded")

    monkeypatch.setattr(
        client_mod,
        "_client",
        SimpleNamespace(start_as_current_observation=_boom),
    )
    assert generation("g", "m") is None
    assert span("s", "m") is None


# ------------------------------------------------- trace-attribute wrapper


class _FakeCM:
    def __init__(self, value="OBS"):
        self.value = value
        self.entered = False
        self.exit_args = None

    def __enter__(self):
        self.entered = True
        return self.value

    def __exit__(self, *args):
        self.exit_args = args
        return False


@pytest.fixture
def obs_ctx(monkeypatch):
    """Initialised fake client whose observation context we can inspect."""
    ctx = _FakeCM()
    monkeypatch.setattr(
        client_mod,
        "_client",
        SimpleNamespace(start_as_current_observation=lambda **kw: ctx),
    )
    return ctx


def test_identity_propagates_to_trace_attributes(obs_ctx, monkeypatch):
    calls: dict = {}
    prop_cm = _FakeCM(value=None)

    def _propagate(**kwargs):
        calls.update(kwargs)
        return prop_cm

    monkeypatch.setattr(tracing_mod, "propagate_attributes", _propagate)

    ctx = generation("g", "m", user_id="u1", session_id="s1", tags=["t"])
    with ctx as obs:
        # Inner observation entered first, then attributes propagated onto it.
        assert obs == "OBS"
        assert calls == {"user_id": "u1", "session_id": "s1", "tags": ["t"]}
        assert prop_cm.entered

    assert prop_cm.exit_args is not None
    assert obs_ctx.exit_args is not None


def test_no_identity_returns_bare_observation_ctx(obs_ctx):
    # Without attributes there is no wrapper — zero added indirection.
    assert generation("g", "m") is obs_ctx


def test_propagate_failure_degrades_to_unattributed_observation(obs_ctx, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("bad attribute")

    monkeypatch.setattr(tracing_mod, "propagate_attributes", _boom)

    ctx = generation("g", "m", user_id="u1")
    with ctx as obs:
        assert obs == "OBS"  # the traced call proceeds regardless

    assert obs_ctx.exit_args is not None  # inner ctx still closed cleanly


# ------------------------------------------------- metadata normalization


@pytest.fixture
def propagated(obs_ctx, monkeypatch):
    """Capture the kwargs handed to propagate_attributes."""
    calls: dict = {}

    def _propagate(**kwargs):
        calls.update(kwargs)
        return _FakeCM(value=None)

    monkeypatch.setattr(tracing_mod, "propagate_attributes", _propagate)
    return calls


def test_metadata_non_string_values_become_json(propagated):
    # Langfuse str()-coerces propagated values ("{'a': 1}", "True"); we
    # pre-serialise so structure survives as valid JSON text instead.
    with generation(
        "g",
        "m",
        metadata={"tenant": "acme", "flags": {"beta": True}, "attempt": 3},
    ):
        pass

    assert propagated["metadata"] == {
        "tenant": "acme",
        "flags": '{"beta":true}',
        "attempt": "3",
    }


def test_metadata_unserialisable_value_falls_back_to_str(propagated):
    loop: dict = {}
    loop["self"] = loop  # json.dumps raises ValueError on circular refs

    with generation("g", "m", metadata={"weird": loop}):
        pass

    assert propagated["metadata"]["weird"] == str(loop)


def test_metadata_non_string_keys_coerced(propagated):
    with generation("g", "m", metadata={7: "seven"}):
        pass

    assert propagated["metadata"] == {"7": "seven"}
