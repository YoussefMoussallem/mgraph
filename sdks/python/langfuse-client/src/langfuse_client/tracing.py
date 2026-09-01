from __future__ import annotations

import json
import logging

from langfuse import propagate_attributes

from langfuse_client.client import get_client

__all__ = ["generation", "span"]

logger = logging.getLogger(__name__)


class _ObservationWithAttributes:
    """Compose an observation context with ``propagate_attributes`` so
    trace-level identity (user_id, session_id, metadata, tags) lands on the
    observation and every child span created inside it.

    Entered *inside* the observation on purpose: ``propagate_attributes``
    applies to the currently-active span, which at that point is the
    observation itself. Attribute failures degrade to an un-attributed
    observation — never to a broken call.
    """

    def __init__(self, obs_ctx, attrs: dict):
        self._obs_ctx = obs_ctx
        self._attrs = attrs
        self._prop_ctx = None

    def __enter__(self):
        obs = self._obs_ctx.__enter__()
        try:
            self._prop_ctx = propagate_attributes(**self._attrs)
            self._prop_ctx.__enter__()
        except Exception:
            self._prop_ctx = None
            logger.warning("Langfuse propagate_attributes failed", exc_info=True)
        return obs

    def __exit__(self, exc_type, exc, tb):
        if self._prop_ctx is not None:
            try:
                self._prop_ctx.__exit__(exc_type, exc, tb)
            except Exception:
                logger.debug("Langfuse propagate_attributes exit failed", exc_info=True)
        return self._obs_ctx.__exit__(exc_type, exc, tb)


def _normalize_metadata(metadata: dict | None) -> dict | None:
    """Fit a free-form metadata dict to ``propagate_attributes``' contract.

    Langfuse coerces propagated metadata values with ``str()`` — a nested
    dict would land as a Python repr (``"{'a': 1}"``) — and silently drops
    any coerced value over 200 characters. Strings pass through untouched;
    everything else is pre-serialised as compact JSON (``str`` as the
    last-resort encoder) so structure at least survives as valid JSON text.
    The 200-char cap still applies downstream: metadata is for flat, short,
    filterable dimensions, not payloads.
    """
    if not metadata:
        return metadata
    out: dict = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            key = str(key)
        if isinstance(value, str):
            out[key] = value
        else:
            try:
                out[key] = json.dumps(value, separators=(",", ":"), default=str)
            except (TypeError, ValueError):
                out[key] = str(value)
    return out


def _trace_attrs(user_id, session_id, metadata, tags) -> dict:
    return {
        k: v
        for k, v in (
            ("user_id", user_id),
            ("session_id", session_id),
            ("metadata", _normalize_metadata(metadata)),
            ("tags", tags),
        )
        if v
    }


def generation(
    name: str,
    model: str,
    input_data: dict | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
):
    """Langfuse *generation* observation context manager, or None when
    tracing is off. Callers guard with ``if ctx:`` — and a Langfuse failure
    degrades to None too; tracing must never take down the traced call.

    ``user_id`` / ``session_id`` / ``metadata`` / ``tags`` are trace-level
    attributes, propagated to the observation and its children — they are
    what Langfuse aggregations (cost per user, session drill-down, tag
    filters) key on.
    """
    lf = get_client()
    if lf is None:
        return None
    try:
        ctx = lf.start_as_current_observation(
            as_type="generation",
            name=name,
            model=model,
            input=input_data,
        )
    except Exception:
        logger.warning("Langfuse generation observation failed", exc_info=True)
        return None
    attrs = _trace_attrs(user_id, session_id, metadata, tags)
    return _ObservationWithAttributes(ctx, attrs) if attrs else ctx


def span(
    name: str,
    model: str,
    input_data: dict | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
):
    """Langfuse *span* observation context manager, or None when tracing is
    off. ``model`` is folded into the input payload — the span type has no
    first-class model field. Same never-raise contract and trace-attribute
    semantics as ``generation``."""
    lf = get_client()
    if lf is None:
        return None
    payload: dict = {"model": model}
    if input_data:
        payload.update(input_data)
    try:
        ctx = lf.start_as_current_observation(
            as_type="span",
            name=name,
            input=payload,
        )
    except Exception:
        logger.warning("Langfuse span observation failed", exc_info=True)
        return None
    attrs = _trace_attrs(user_id, session_id, metadata, tags)
    return _ObservationWithAttributes(ctx, attrs) if attrs else ctx
