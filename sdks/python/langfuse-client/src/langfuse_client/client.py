from __future__ import annotations

import logging
import os
import threading

import httpx
from langfuse import Langfuse

__all__ = ["flush", "get_client", "init_client", "shutdown"]

logger = logging.getLogger(__name__)

# Lifecycle state owned by THIS module, not read back from langfuse:
# ``langfuse.get_client()`` never returns None — uninitialised it constructs
# a client on the fly (background threads, OTel setup, warning logs), and
# with two projects registered it returns a DISABLED client. Tracking our
# own handle keeps "not initialised" a real, cheap no-op state.
_client: Langfuse | None = None
_public_key: str | None = None
_lock = threading.Lock()


def init_client(
    public_key: str,
    secret_key: str,
    base_url: str = "https://cloud.langfuse.com",
    httpx_client: httpx.Client | None = None,
    additional_headers: dict | None = None,
    cacert_path: str | None = None,
    proxy_token: str | None = None,
) -> Langfuse:
    """Initialise the process-wide Langfuse client and return it.

    Thread-safe and idempotent: repeat calls with the same ``public_key``
    return the existing client; a different key is ignored with a warning
    (a second Langfuse project in one process makes the SDK disable bare
    ``get_client()`` lookups, silently killing tracing).

    ``cacert_path`` / ``proxy_token`` cover both transports — the httpx
    REST client and the OTLP span exporter (via
    ``OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE``; deployment-set OTel vars
    always win). Note: construction succeeding does not prove the
    credentials are valid — Langfuse validates lazily in the background.

    Raises ValueError on empty credentials or a ``cacert_path`` that
    doesn't exist.
    """
    global _client, _public_key

    if not public_key or not secret_key:
        raise ValueError("init_client requires non-empty public_key and secret_key")
    if cacert_path and not os.path.isfile(cacert_path):
        raise ValueError(f"cacert_path does not exist: {cacert_path}")

    with _lock:
        if _client is not None:
            if public_key == _public_key:
                return _client
            logger.warning(
                "langfuse_client already initialised with a different public "
                "key; keeping the existing client (re-init ignored)"
            )
            return _client

        headers = dict(additional_headers or {})
        if proxy_token:
            headers.setdefault("Proxy-Authorization", proxy_token)

        if httpx_client is None and (cacert_path or headers):
            httpx_client = httpx.Client(
                verify=cacert_path if cacert_path else True,
                headers=headers or None,
            )

        if cacert_path and not (
            os.environ.get("OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE")
            or os.environ.get("OTEL_EXPORTER_OTLP_CERTIFICATE")
        ):
            # Trace-scoped, so exporters for other signals are untouched;
            # skipped when the deployment configured either OTel cert var
            # (the traces var outranks the general one, so a bare setdefault
            # could override a deployment that only set the general var).
            os.environ["OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE"] = cacert_path

        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url.rstrip("/"),
            httpx_client=httpx_client,
            additional_headers=headers or None,
        )
        _public_key = public_key
        return _client


def get_client() -> Langfuse | None:
    """The client from ``init_client``, or None. Never constructs implicitly."""
    return _client


def flush() -> None:
    """Flush pending spans to Langfuse; no-op when uninitialised.

    Spans export from a background batch thread — call this before points
    where the process might exit (end of a batch job, worker drain).
    """
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            logger.warning("Langfuse flush failed", exc_info=True)


def shutdown() -> None:
    """Flush and permanently shut down tracing; no-op when uninitialised.

    Terminal for the process — call at exit (short-lived scripts lose
    unexported spans without it). Afterwards ``get_client()`` returns None
    and the tracing helpers no-op.
    """
    global _client, _public_key
    if _client is None:
        return
    try:
        _client.shutdown()
    except Exception:
        logger.warning("Langfuse shutdown failed", exc_info=True)
    finally:
        _client = None
        _public_key = None
