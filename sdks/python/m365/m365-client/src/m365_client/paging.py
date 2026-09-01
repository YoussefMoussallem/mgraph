"""Walk Graph's ``@odata.nextLink`` paging, once, for every consumer.

Why this lives in the SDK when workload code does not
----------------------------------------------------
This package deliberately ships no workload helpers -- no ``list_messages()``,
no ``get_site()``. Paging is not workload code though: it is Graph *protocol*
plumbing, identical for messages, drive items, sites, and everything else.

The alternative is worse. ``CONTRIBUTING.md`` section 1 forbids shared runtime
code between service folders, so without this every consuming service
hand-rolls the same ``while response.odata_next_link`` loop, and they drift --
one forgets the page cap, another re-sends ``$top`` on follow-up requests (which
Graph rejects, because the next link already encodes it), a third leaks an
unbounded loop on a malformed response.

So: no workload knowledge here, but the one piece of Graph mechanics every
workload needs.

Usage
-----
Both helpers take the *request builder* and a callable that fetches a page, so
they stay agnostic of which collection is being read::

    from m365_client import iter_pages, collect

    # stream items as they arrive, never holding the whole set in memory
    async for message in iter_pages(
        client.me.messages,
        lambda b: b.get(request_configuration=config),
    ):
        ...

    # or materialise a bounded list
    messages = await collect(
        client.me.messages,
        lambda b: b.get(request_configuration=config),
        max_items=200,
    )

The ``request_configuration`` is applied to the **first** request only, which is
correct: Graph encodes ``$select``, ``$filter``, ``$top`` and friends into the
next link itself, and re-sending them alongside it is an error.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, TypeVar

from m365_client.errors import GraphError

__all__ = ["DEFAULT_TOP", "MAX_TOP", "check_top", "collect", "iter_pages"]

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A page beyond which we assume something is wrong rather than keep going.
# Graph has been known to return a next link that resolves to itself; without a
# ceiling that is an infinite loop holding a request open forever.
DEFAULT_MAX_PAGES = 100

#: Ceiling on how many items one list call may return. This is the platform
#: API rule -- no list endpoint returns more than 50 per call (CONTRIBUTING
#: section 6) -- kept in exactly one place so every workload SDK enforces the
#: same number through :func:`check_top`. A consumer that genuinely needs a
#: bigger walk streams with :func:`iter_pages`, deliberately.
MAX_TOP = 50
#: Default ``top`` for a list call when the caller does not choose one.
DEFAULT_TOP = 10


def check_top(top: int, *, maximum: int = MAX_TOP) -> None:
    """Reject an out-of-range ``top`` before any network call.

    A caller bug should fail fast with a plain ``ValueError``, not surface as
    a Graph 400 after a round trip. ``bool`` is excluded explicitly: ``True``
    is an ``int`` in Python and would otherwise sail through as ``top=1``.

    Raises:
        ValueError: ``top`` is not an integer from 1 to ``maximum``.
    """
    if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= maximum:
        raise ValueError(f"top must be an integer from 1 to {maximum}, got {top!r}")


class _RequestBuilder(Protocol):
    """The slice of a Kiota request builder this module needs."""

    def with_url(self, raw_url: str) -> Any:  # pragma: no cover - structural
        ...


async def iter_pages(
    builder: _RequestBuilder,
    fetch: Callable[[Any], Awaitable[Any]],
    *,
    max_items: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> AsyncIterator[Any]:
    """Yield every item across every page, following ``@odata.nextLink``.

    Streams rather than accumulating, so a large collection never sits in
    memory at once. Wrap the iteration in
    :func:`~m365_client.errors.translate_graph_errors` -- each page is a
    separate HTTP request and any of them can fail.

    Args:
        builder: The Kiota request builder for the collection, e.g.
            ``client.me.messages``. Used to rebuild a request against each
            next link via ``with_url``.
        fetch: Called with a builder, returns the awaitable page. A lambda is
            the usual shape, so per-request configuration can be closed over:
            ``lambda b: b.get(request_configuration=config)``. Applied to the
            first page only -- see the module docstring.
        max_items: Stop after this many items. ``None`` means no limit.
        max_pages: Safety ceiling on page count. Exceeding it logs a warning
            and stops rather than raising, so a caller still gets the items it
            did retrieve.

    Yields:
        Items from each page's ``value``, in page order.

    Raises:
        GraphError: If a response has no ``value`` attribute, which means the
            endpoint is not a collection and the caller has the wrong helper.
    """
    if max_items is not None and max_items <= 0:
        return

    current = builder
    pages = 0
    yielded = 0
    first = True

    while True:
        page = await fetch(current) if first else await current.get()
        first = False
        pages += 1

        if page is None:
            return

        if not hasattr(page, "value"):
            raise GraphError(
                "response has no 'value' collection -- iter_pages expects a "
                f"collection endpoint, got {type(page).__name__}"
            )

        for item in page.value or []:
            yield item
            yielded += 1
            if max_items is not None and yielded >= max_items:
                return

        next_link = getattr(page, "odata_next_link", None)
        if not next_link:
            return

        if pages >= max_pages:
            # Deliberately not an exception: the caller's items are valid, and
            # failing the whole request because page 101 exists would be worse
            # than returning 100 pages with a loud log line.
            logger.warning(
                "iter_pages stopped at the %d-page ceiling with a next link "
                "still present; raise max_pages if this collection is "
                "legitimately larger",
                max_pages,
            )
            return

        current = builder.with_url(next_link)


async def collect(
    builder: _RequestBuilder,
    fetch: Callable[[Any], Awaitable[Any]],
    *,
    max_items: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[Any]:
    """Materialise :func:`iter_pages` into a list.

    Convenience for the common case of a bounded result set feeding a response
    model. Pass ``max_items`` for anything user-facing -- an endpoint that
    happily returns every message in a mailbox is a latency incident waiting to
    happen, and the API design rules cap page size at 50 anyway.
    """
    return [
        item
        async for item in iter_pages(
            builder, fetch, max_items=max_items, max_pages=max_pages
        )
    ]
