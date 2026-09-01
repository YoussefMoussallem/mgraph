"""Shared plumbing for every tool class: the Graph seam and result shaping.

The one design decision that matters lives here. **Identity never comes from
the model.** Each tool holds a ``graph_provider`` — an async factory the host
application builds from trusted runtime context and binds at construction
time — and asks it for a ``GraphServiceClient`` per invocation. The provider
is excluded from serialization, so nothing about it reaches the provider-
facing function schema.

The host decides whose permissions the tools run under by deciding what the
provider returns. The intended shape is delegated::

    provider = lambda: m365.graph_for_user(assertion, user_oid)

so Microsoft Graph enforces the signed-in user's own permissions and the
tools can never read anything the user could not — and every write lands
under that user's name.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from langchain_core.tools import BaseTool
from m365_client import (
    GraphAuthError,
    GraphConflictError,
    GraphError,
    GraphInvalidRequestError,
    GraphNotFoundError,
    GraphThrottledError,
)
from msgraph import GraphServiceClient
from pydantic import Field

__all__ = [
    "MAX_TEXT_CHARS",
    "RECOVERABLE_ERRORS",
    "GraphProvider",
    "M365BaseTool",
    "dump_json",
    "is_text_mime",
    "recoverable_error_text",
]

#: The trusted-context seam: an async factory returning a Graph client for
#: the identity this agent execution acts as. Built by the host, never by a
#: tool, and never influenced by a model argument.
GraphProvider = Callable[[], Awaitable[GraphServiceClient]]

#: Failures the model can act on, including the SDK's own argument checks
#: (``ValueError``: a malformed address, a bad body type, an empty id).
#: Everything else propagates and is recorded by the host as a failed tool
#: call — a configuration problem is not model-fixable.
RECOVERABLE_ERRORS = (
    GraphAuthError,
    GraphConflictError,
    GraphInvalidRequestError,
    GraphNotFoundError,
    GraphThrottledError,
    ValueError,
)

#: Upper bound any text-returning tool accepts for ``max_chars``.
MAX_TEXT_CHARS = 50_000

# Formats whose bytes decode into model-readable text. Office documents
# (.docx, .xlsx, .pptx) are ZIP containers and deliberately refused — their
# bytes are useless in a model context.
_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/javascript",
        "application/x-javascript",
        "application/yaml",
        "application/x-yaml",
        "application/csv",
        "application/sql",
        "application/x-sh",
        "application/x-python",
    }
)


def is_text_mime(mime_type: str | None) -> bool:
    """Whether bytes of this MIME type are worth decoding for a model."""
    if not mime_type:
        return False
    mime = mime_type.split(";", 1)[0].strip().lower()
    return (
        mime.startswith("text/") or mime in _TEXT_MIME_EXACT or mime.endswith(("+json", "+xml"))
    )


def dump_json(payload: Any) -> str:
    """Serialize a result for the model: real JSON, datetimes as ISO strings.

    Returning a dict from a tool is a trap in some hosts — Apex, for one,
    renders unrecognized dicts with ``str()``, producing a Python repr rather
    than JSON — so every tool in this package serializes its own result.
    """
    if is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    return json.dumps(payload, default=str, ensure_ascii=False)


def recoverable_error_text(exc: Exception) -> str:
    """Turn a recoverable failure into text the model can act on.

    Concise, actionable, and free of stack traces, connection details, or
    anything else the model has no use for.
    """
    if isinstance(exc, ValueError) and not isinstance(exc, GraphError):
        return f"Error: {exc}. Correct the arguments and call again."
    if isinstance(exc, GraphNotFoundError):
        return (
            f"Not found: {exc} The ID may be wrong, or the signed-in user cannot see this "
            "item. Use IDs exactly as returned by an earlier tool result — they cannot be "
            "guessed or shortened."
        )
    if isinstance(exc, GraphThrottledError):
        retry = f" Retry after {exc.retry_after} seconds." if exc.retry_after else ""
        return (
            f"Microsoft Graph throttled this request.{retry} Make fewer or smaller calls "
            "before trying again."
        )
    if isinstance(exc, GraphConflictError):
        return (
            f"Conflict: {exc} Something with that name already exists, or the item changed "
            "since it was read. Choose a different name, or re-read the item and try again."
        )
    if isinstance(exc, GraphInvalidRequestError):
        return f"Microsoft Graph rejected the request: {exc} Adjust the arguments and call again."
    return (
        "Microsoft Graph denied access: the signed-in user does not have permission for "
        "this data or action, or the required delegated permission has not been granted "
        "to the application. Retrying with the same arguments will not help."
    )


class M365BaseTool(BaseTool):
    """Base for every tool here: async-only, with the bound Graph seam.

    Construct one fresh instance per agent execution (the package factories
    do this) and never share instances across concurrent executions — the
    bound provider is execution-specific state.
    """

    #: Excluded from serialization so it can never leak into the
    #: provider-facing schema or a trace payload.
    graph_provider: GraphProvider | None = Field(default=None, exclude=True)

    def _run(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError(f"{self.name} is async-only; invoke it through the async path")

    async def _graph(self) -> GraphServiceClient:
        """The Graph client for this invocation, from trusted context only.

        A missing provider is a wiring bug in the host, not a caller error,
        so it raises rather than returning model-visible text.
        """
        if self.graph_provider is None:
            raise RuntimeError(
                f"{self.name} has no Microsoft Graph access bound. Construct it through "
                "m365_langchain_tools.outlook_tools()/sharepoint_tools() with a "
                "graph_provider built from trusted runtime context."
            )
        return await self.graph_provider()
