"""``init_client`` lifecycle + corporate-proxy plumbing.

Pins the edge-case contract: validation fails fast, init is thread-safe and
idempotent (a second project key is refused — it would trip Langfuse's
multi-project safety and silently disable tracing), ``get_client`` never
constructs implicitly, flush/shutdown are safe no-ops when uninitialised,
and ``cacert_path``/``proxy_token`` cover BOTH transports (httpx REST client
+ OTLP exporter via ``OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE``, deferring to
deployment-set OTel vars).
"""

from __future__ import annotations

import logging
import os

import langfuse_client.client as client_mod
import pytest
from langfuse_client import flush, get_client, init_client, shutdown

OTEL_CERT_TRACES = "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE"
OTEL_CERT_GENERAL = "OTEL_EXPORTER_OTLP_CERTIFICATE"


class _FakeHttpxClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def captured(monkeypatch):
    """Stub Langfuse + httpx.Client, reset module state; capture constructions."""
    calls: dict = {"instances": []}

    class _FakeLangfuse:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.flushed = 0
            self.shut_down = 0
            calls.update(kwargs)
            calls["instances"].append(self)

        def flush(self):
            self.flushed += 1

        def shutdown(self):
            self.shut_down += 1

    monkeypatch.setattr(client_mod, "Langfuse", _FakeLangfuse)
    monkeypatch.setattr(client_mod.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(client_mod, "_client", None)
    monkeypatch.setattr(client_mod, "_public_key", None)
    monkeypatch.delenv(OTEL_CERT_TRACES, raising=False)
    monkeypatch.delenv(OTEL_CERT_GENERAL, raising=False)
    return calls


@pytest.fixture
def pem(tmp_path):
    """A CA bundle path that exists — cacert_path is validated with isfile."""
    p = tmp_path / "corp-ca.pem"
    p.write_text("dummy")
    return str(p)


# ------------------------------------------------------------- happy paths


def test_plain_init_builds_no_client_and_sets_no_env(captured):
    client = init_client(public_key="pk", secret_key="sk")
    assert captured["httpx_client"] is None
    assert captured["additional_headers"] is None
    assert OTEL_CERT_TRACES not in os.environ
    assert OTEL_CERT_GENERAL not in os.environ
    # Returns the client, and get_client() hands back the same one.
    assert client is captured["instances"][0]
    assert get_client() is client


def test_cacert_and_token_cover_both_transports(captured, pem):
    init_client(
        public_key="pk",
        secret_key="sk",
        cacert_path=pem,
        proxy_token="Bearer t",
    )
    # REST path: httpx client built with the CA bundle and the proxy header.
    hc = captured["httpx_client"]
    assert hc.kwargs["verify"] == pem
    assert hc.kwargs["headers"]["Proxy-Authorization"] == "Bearer t"
    # OTLP path: trace-scoped env var for the exporter + merged headers.
    assert os.environ[OTEL_CERT_TRACES] == pem
    assert captured["additional_headers"]["Proxy-Authorization"] == "Bearer t"


def test_token_without_cacert_still_builds_headers(captured):
    init_client(public_key="pk", secret_key="sk", proxy_token="Bearer t")
    hc = captured["httpx_client"]
    assert hc.kwargs["verify"] is True  # default trust store
    assert hc.kwargs["headers"]["Proxy-Authorization"] == "Bearer t"
    assert OTEL_CERT_TRACES not in os.environ


def test_base_url_trailing_slash_stripped(captured):
    init_client(public_key="pk", secret_key="sk", base_url="https://lf.internal/")
    assert captured["base_url"] == "https://lf.internal"


# -------------------------------------------------------------- validation


def test_empty_credentials_raise(captured):
    with pytest.raises(ValueError):
        init_client(public_key="", secret_key="sk")
    with pytest.raises(ValueError):
        init_client(public_key="pk", secret_key="")
    assert captured["instances"] == []  # nothing constructed


def test_missing_cacert_file_raises(captured):
    with pytest.raises(ValueError, match="cacert_path"):
        init_client(public_key="pk", secret_key="sk", cacert_path="no/such.pem")
    assert captured["instances"] == []
    assert OTEL_CERT_TRACES not in os.environ


# ------------------------------------------------------------- re-init


def test_reinit_same_key_is_idempotent(captured):
    first = init_client(public_key="pk", secret_key="sk")
    second = init_client(public_key="pk", secret_key="sk")
    assert second is first
    assert len(captured["instances"]) == 1


def test_reinit_different_key_keeps_first_and_warns(captured, caplog):
    first = init_client(public_key="pk-1", secret_key="sk")
    with caplog.at_level(logging.WARNING, logger="langfuse_client.client"):
        second = init_client(public_key="pk-2", secret_key="sk")
    assert second is first
    assert len(captured["instances"]) == 1
    assert "different public key" in caplog.text


# ------------------------------------------------------- OTel var deference


def test_deployment_traces_var_wins(captured, pem, monkeypatch):
    monkeypatch.setenv(OTEL_CERT_TRACES, "deployment.pem")
    init_client(public_key="pk", secret_key="sk", cacert_path=pem)
    assert os.environ[OTEL_CERT_TRACES] == "deployment.pem"


def test_deployment_general_var_blocks_sdk_traces_var(captured, pem, monkeypatch):
    # The traces-specific var OUTRANKS the general one in the exporter, so
    # the SDK must not set it when the deployment configured the general var
    # — doing so would silently override platform OTel config.
    monkeypatch.setenv(OTEL_CERT_GENERAL, "platform.pem")
    init_client(public_key="pk", secret_key="sk", cacert_path=pem)
    assert OTEL_CERT_TRACES not in os.environ
    assert os.environ[OTEL_CERT_GENERAL] == "platform.pem"


# ---------------------------------------------------------- passthroughs


def test_explicit_proxy_authorization_header_wins(captured):
    init_client(
        public_key="pk",
        secret_key="sk",
        additional_headers={"Proxy-Authorization": "explicit"},
        proxy_token="Bearer t",
    )
    assert captured["additional_headers"]["Proxy-Authorization"] == "explicit"


def test_custom_httpx_client_is_passed_through(captured, pem):
    mine = object()  # opaque — init_client must not replace or wrap it
    init_client(public_key="pk", secret_key="sk", httpx_client=mine, cacert_path=pem)
    assert captured["httpx_client"] is mine
    # OTLP side is still covered even with a caller-supplied client.
    assert os.environ[OTEL_CERT_TRACES] == pem


# ------------------------------------------------------- flush / shutdown


def test_flush_and_shutdown_are_noops_when_uninitialised(captured):
    flush()
    shutdown()  # neither raises
    assert get_client() is None


def test_flush_and_shutdown_lifecycle(captured):
    client = init_client(public_key="pk", secret_key="sk")
    flush()
    assert client.flushed == 1

    shutdown()
    assert client.shut_down == 1
    assert get_client() is None  # terminal: helpers no-op again

    shutdown()  # second shutdown is a safe no-op
    assert client.shut_down == 1


def test_flush_and_shutdown_swallow_langfuse_errors(captured):
    client = init_client(public_key="pk", secret_key="sk")

    def _boom():
        raise RuntimeError("exporter died")

    client.flush = _boom
    client.shutdown = _boom
    flush()  # logged, not raised
    shutdown()  # logged, not raised — and state still cleared
    assert get_client() is None
