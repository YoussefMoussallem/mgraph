"""SharePoint access over Microsoft Graph: sites, libraries, files, lists.

:class:`SharePointClient` wraps a ``GraphServiceClient`` and exposes the
operations a platform app needs -- find a site, walk its document libraries
and folders, read, upload, move and delete files, search a library, read
lists -- as typed, paged, error-translated calls. Token acquisition, caching,
and HTTP configuration are ``m365_client``'s job; this class only knows which
Graph calls to make and how to map the results.

Delegated by intent
-------------------
Which identity the calls run as is decided by the ``GraphServiceClient`` the
caller passes in. ``M365Client.graph_for_user()`` gives one acting as the
signed-in user, and that is the intended use: Graph then returns only the
sites and files that user can already see, and a write lands under that
user's name, so application code is not the permission boundary. This
matters more for SharePoint than anywhere else -- app-only ``Sites.*.All``
is tenant-wide, enterprises reject it, and the ``Sites.Selected``
alternative needs a per-site grant process. Delegated access needs none of
that. This package never reaches for the app-only path itself.

Graph delegated permissions by operation group:

==============================  ==========================================
Sites, drives, files, lists     ``Sites.Read.All``, ``Files.Read.All``
Upload, folders, move, delete   ``Sites.ReadWrite.All``, ``Files.ReadWrite.All``
==============================  ==========================================
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from kiota_abstractions.base_request_configuration import RequestConfiguration
from m365_client import (
    DEFAULT_TOP,
    MAX_TOP,
    GraphError,
    GraphNotFoundError,
    check_top,
    collect,
    translate_graph_errors,
)
from msgraph import GraphServiceClient
from msgraph.generated.drives.item.items.item.children.children_request_builder import (
    ChildrenRequestBuilder,
)
from msgraph.generated.drives.item.items.items_request_builder import (
    ItemsRequestBuilder,
)
from msgraph.generated.drives.item.search_with_q.search_with_q_request_builder import (
    SearchWithQRequestBuilder,
)
from msgraph.generated.models.drive_item import DriveItem as GraphDriveItem
from msgraph.generated.models.folder import Folder
from msgraph.generated.models.item_reference import ItemReference
from msgraph.generated.sites.item.drives.drives_request_builder import (
    DrivesRequestBuilder,
)
from msgraph.generated.sites.item.lists.item.items.items_request_builder import (
    ItemsRequestBuilder as ListItemsRequestBuilder,
)
from msgraph.generated.sites.item.lists.lists_request_builder import (
    ListsRequestBuilder,
)
from msgraph.generated.sites.item.site_item_request_builder import (
    SiteItemRequestBuilder,
)
from msgraph.generated.sites.sites_request_builder import SitesRequestBuilder

from sharepoint_client.models import (
    Drive,
    DriveItem,
    Identity,
    ListItemRecord,
    SharePointList,
    Site,
)

# ``MAX_TOP`` and ``DEFAULT_TOP`` are the platform-wide list caps from
# ``m365_client.paging``, re-exported so consumers can bound their own query
# parameters against the same number this client enforces.
__all__ = ["DEFAULT_TOP", "MAX_TOP", "MAX_UPLOAD_BYTES", "SharePointClient"]

#: Graph's ceiling for a single-request ("simple") upload; larger files need
#: an upload session, which this client does not implement.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

_CONFLICT_BEHAVIOURS = frozenset({"fail", "replace", "rename"})
_CONFLICT_KEY = "@microsoft.graph.conflictBehavior"

_SITE_SELECT = ["id", "displayName", "name", "webUrl", "createdDateTime", "lastModifiedDateTime"]

_DRIVE_SELECT = [
    "id",
    "name",
    "driveType",
    "webUrl",
    "createdDateTime",
    "lastModifiedDateTime",
    "owner",
    "quota",
]

# Requesting only what is mapped keeps drive-item payloads small; a full
# DriveItem carries a lot of facets nobody here reads.
_ITEM_SELECT = [
    "id",
    "name",
    "size",
    "createdDateTime",
    "lastModifiedDateTime",
    "webUrl",
    "file",
    "folder",
    "createdBy",
    "lastModifiedBy",
    "parentReference",
]

_LIST_SELECT = ["id", "name", "displayName", "webUrl", "description", "list"]


# ── Argument checks ──────────────────────────────────────────────────────────


def _check_id(value: str, name: str) -> None:
    """An empty id would silently turn an item request into a collection request."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _check_name(name: str) -> str:
    """A file or folder name: one path segment, no separators."""
    _check_id(name, "name")
    clean = name.strip()
    if "/" in clean or chr(92) in clean:
        raise ValueError(f"name must be a single path segment without slashes, got {name!r}")
    return clean


def _check_conflict(conflict: str) -> str:
    if conflict not in _CONFLICT_BEHAVIOURS:
        raise ValueError(
            f"conflict must be one of {sorted(_CONFLICT_BEHAVIOURS)}, got {conflict!r}"
        )
    return conflict


def _clean_path(path: str | None) -> str | None:
    """Drive-relative folder path with separators kept and everything else encoded."""
    if not path or not path.strip("/ "):
        return None
    return quote(path.strip().strip("/"), safe="/")


# ── Mapping from Graph models ────────────────────────────────────────────────


def _identity(identity_set: Any) -> Identity | None:
    """Map an ``IdentitySet`` (``createdBy``, ``owner``, ...) to :class:`Identity`.

    The generated ``Identity`` class declares ``display_name`` and ``id`` only.
    Real Graph responses commonly include ``email`` (occasionally ``mail``),
    and Kiota lands any property it does not recognise in ``additional_data``
    rather than dropping it -- so that is where the address has to be read
    from. Reading only ``user.email`` returns ``None`` every time.
    """
    user = getattr(identity_set, "user", None) if identity_set is not None else None
    if user is None:
        return None
    additional = getattr(user, "additional_data", None) or {}
    email = getattr(user, "email", None) or additional.get("email") or additional.get("mail")
    return Identity(display_name=user.display_name, email=email)


def _site(site: Any) -> Site:
    return Site(
        id=site.id or "",
        display_name=site.display_name,
        name=site.name,
        web_url=site.web_url,
        created_at=site.created_date_time,
        last_modified_at=site.last_modified_date_time,
    )


def _drive(drive: Any) -> Drive:
    quota = getattr(drive, "quota", None)
    return Drive(
        id=drive.id or "",
        name=drive.name,
        drive_type=drive.drive_type,
        web_url=drive.web_url,
        created_at=drive.created_date_time,
        last_modified_at=drive.last_modified_date_time,
        owner=_identity(getattr(drive, "owner", None)),
        quota_used=quota.used if quota else None,
        quota_total=quota.total if quota else None,
    )


def _item(item: Any) -> DriveItem:
    file_facet = getattr(item, "file", None)
    folder_facet = getattr(item, "folder", None)
    parent = getattr(item, "parent_reference", None)
    return DriveItem(
        id=item.id or "",
        name=item.name,
        size=item.size,
        created_at=item.created_date_time,
        last_modified_at=item.last_modified_date_time,
        web_url=item.web_url,
        mime_type=file_facet.mime_type if file_facet else None,
        is_folder=folder_facet is not None,
        child_count=folder_facet.child_count if folder_facet else None,
        created_by=_identity(getattr(item, "created_by", None)),
        last_modified_by=_identity(getattr(item, "last_modified_by", None)),
        parent_id=getattr(parent, "id", None) if parent else None,
    )


def _list(value: Any) -> SharePointList:
    info = getattr(value, "list_", None)
    return SharePointList(
        id=value.id or "",
        name=value.name,
        display_name=value.display_name,
        web_url=value.web_url,
        description=value.description,
        template=getattr(info, "template", None) if info else None,
        hidden=getattr(info, "hidden", None) if info else None,
    )


def _list_item(value: Any) -> ListItemRecord:
    fields_obj = getattr(value, "fields", None)
    raw = dict(getattr(fields_obj, "additional_data", None) or {}) if fields_obj else {}
    return ListItemRecord(
        id=value.id or "",
        web_url=value.web_url,
        created_at=value.created_date_time,
        last_modified_at=value.last_modified_date_time,
        fields={k: v for k, v in raw.items() if not k.startswith("@odata")},
    )


# ── Client ───────────────────────────────────────────────────────────────────


class SharePointClient:
    """Site, library, file and list calls on behalf of whoever ``graph`` acts as.

    Cheap to construct -- it holds a reference and nothing else -- so build one
    per request from the per-request Graph client::

        graph = await m365.graph_for_user(user.assertion, user.user_id)
        sp = SharePointClient(graph)
        sites = await sp.search_sites("intranet")
        drives = await sp.list_drives(sites[0].id)
        items = await sp.list_items(drives[0].id, path="Reports/2026")
        await sp.upload_file(drives[0].id, name="summary.md", content=b"# Q3", parent_path="Reports/2026")

    Attributes:
        graph: The underlying ``GraphServiceClient``, public on purpose. This
            class covers the common operations; anything else -- permissions,
            delta queries, upload sessions -- is one call away on the
            official client.
    """

    def __init__(self, graph: GraphServiceClient) -> None:
        self.graph = graph

    @property
    def _base_url(self) -> str:
        return self.graph.request_adapter.base_url  # type: ignore[attr-defined]

    # ---- sites ------------------------------------------------------------

    async def search_sites(self, query: str | None = None, *, top: int = DEFAULT_TOP) -> list[Site]:
        """Sites the user can see, optionally filtered by a search term.

        Args:
            query: Search text matched against site names. ``None`` (or blank)
                sends Graph's ``*`` wildcard, which returns the sites the user
                has recently been active in.
            top: Maximum number of sites to return, 1 to :data:`MAX_TOP`. A
                total cap rather than a page size -- Graph pages are followed
                until this many have been collected.

        Raises:
            ValueError: ``top`` is out of range.
        """
        check_top(top)
        params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters(
            search=query.strip() if query and query.strip() else "*",
            top=top,
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            items = await collect(
                self.graph.sites,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_site(s) for s in items]

    async def get_site(self, site_id: str) -> Site:
        """Metadata for one site.

        Raises:
            ValueError: ``site_id`` is empty.
            GraphNotFoundError: The site does not exist or is not visible to
                this user. Graph answers 404 rather than 403 for sites the
                user cannot see, and that distinction is preserved.
        """
        _check_id(site_id, "site_id")
        params = SiteItemRequestBuilder.SiteItemRequestBuilderGetQueryParameters(
            select=_SITE_SELECT,
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            site = await self.graph.sites.by_site_id(site_id).get(request_configuration=config)
        if site is None:
            raise GraphNotFoundError(f"Site {site_id} not found", status_code=404)
        return _site(site)

    # ---- drives -----------------------------------------------------------

    async def list_drives(self, site_id: str, *, top: int = DEFAULT_TOP) -> list[Drive]:
        """Document libraries of a site.

        Raises:
            ValueError: ``site_id`` is empty or ``top`` is out of range.
            GraphNotFoundError: The site does not exist or is not visible.
        """
        _check_id(site_id, "site_id")
        check_top(top)
        params = DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters(
            top=top,
            select=_DRIVE_SELECT,
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            items = await collect(
                self.graph.sites.by_site_id(site_id).drives,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_drive(d) for d in items]

    # ---- items: read --------------------------------------------------------

    async def list_items(
        self,
        drive_id: str,
        *,
        path: str | None = None,
        item_id: str | None = None,
        top: int = DEFAULT_TOP,
    ) -> list[DriveItem]:
        """Children of a folder in a drive.

        Args:
            drive_id: The library, from :meth:`list_drives`.
            path: A folder path relative to the drive root, e.g.
                ``"Reports/2026"``. Segments are percent-encoded here, so a
                name with spaces or ``#`` is safe to pass as-is.
            item_id: A folder's item id, from an earlier listing. Mutually
                exclusive with ``path``.
            top: Maximum number of items to return, 1 to :data:`MAX_TOP`.

        With neither ``path`` nor ``item_id`` the drive root is listed.

        Raises:
            ValueError: Bad arguments, including both ``path`` and ``item_id``.
            GraphNotFoundError: The drive or folder does not exist or is not
                visible.
        """
        _check_id(drive_id, "drive_id")
        check_top(top)
        clean = _clean_path(path)
        if clean and item_id:
            raise ValueError("pass either path or item_id, not both")

        drive = self.graph.drives.by_drive_id(drive_id)
        query: Any
        if item_id:
            builder = drive.items.by_drive_item_id(item_id).children
            query = ChildrenRequestBuilder.ChildrenRequestBuilderGetQueryParameters(
                top=top, select=_ITEM_SELECT
            )
        elif clean:
            # Path-based addressing -- ``/drives/{id}/root:/{path}:/children`` --
            # has no generated builder, so the URL is composed and handed to
            # ``with_url``. The collection endpoint parses with the items
            # builder, whose ``get`` expects a collection.
            builder = drive.items.with_url(
                f"{self._base_url}/drives/{drive_id}/root:/{clean}:/children"
            )
            query = ItemsRequestBuilder.ItemsRequestBuilderGetQueryParameters(
                top=top, select=_ITEM_SELECT
            )
        else:
            builder = drive.items.by_drive_item_id("root").children
            query = ChildrenRequestBuilder.ChildrenRequestBuilderGetQueryParameters(
                top=top, select=_ITEM_SELECT
            )
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            items = await collect(
                builder,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_item(i) for i in items]

    async def get_item(self, drive_id: str, item_id: str) -> DriveItem:
        """Metadata for one file or folder.

        Raises:
            ValueError: An id is empty.
            GraphNotFoundError: The item does not exist or is not visible.
        """
        _check_id(drive_id, "drive_id")
        _check_id(item_id, "item_id")

        async with translate_graph_errors():
            item = await self.graph.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()
        if item is None:
            raise GraphNotFoundError(f"Item {item_id} not found", status_code=404)
        return _item(item)

    async def get_item_by_path(self, drive_id: str, path: str) -> DriveItem:
        """Metadata for the file or folder at a drive-relative path.

        Raises:
            ValueError: ``drive_id`` or ``path`` is empty.
            GraphNotFoundError: Nothing exists at that path, or it is not
                visible.
        """
        _check_id(drive_id, "drive_id")
        clean = _clean_path(path)
        if clean is None:
            raise ValueError("path is required")
        # A single item at a path -- ``/drives/{id}/root:/{path}:`` -- parses
        # with the *item* builder; ``with_url`` on it keeps that parser.
        builder = (
            self.graph.drives.by_drive_id(drive_id)
            .items.by_drive_item_id("root")
            .with_url(f"{self._base_url}/drives/{drive_id}/root:/{clean}:")
        )
        async with translate_graph_errors():
            item = await builder.get()
        if item is None:
            raise GraphNotFoundError(f"Nothing at path {path!r}", status_code=404)
        return _item(item)

    async def download_item(self, drive_id: str, item_id: str) -> bytes:
        """The content of a file, fully buffered.

        The generated SDK's ``content.get()`` reads the whole body into memory
        before returning -- there is no streaming variant in the generated
        client -- so this is suitable for documents, not multi-gigabyte
        downloads. A consumer that needs true streaming should fetch the
        item's ``@microsoft.graph.downloadUrl`` and stream that itself.

        Raises:
            ValueError: An id is empty.
            GraphNotFoundError: The item does not exist, is not visible, or
                has no content (a folder).
        """
        _check_id(drive_id, "drive_id")
        _check_id(item_id, "item_id")

        async with translate_graph_errors():
            content = (
                await self.graph.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(item_id)
                .content.get()
            )
        if content is None:
            raise GraphNotFoundError(
                f"Item {item_id} has no downloadable content", status_code=404
            )
        return content

    async def search_files(self, drive_id: str, query: str, *, top: int = DEFAULT_TOP) -> list[DriveItem]:
        """Files and folders in a drive whose name or content matches ``query``.

        Graph's drive search is full-text over names and indexed content,
        ranked by relevance; it may return items from any folder depth.

        Raises:
            ValueError: An argument is empty or ``top`` is out of range.
            GraphNotFoundError: The drive does not exist or is not visible.
        """
        _check_id(drive_id, "drive_id")
        _check_id(query, "query")
        check_top(top)
        params = SearchWithQRequestBuilder.SearchWithQRequestBuilderGetQueryParameters(
            top=top, select=_ITEM_SELECT
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            items = await collect(
                self.graph.drives.by_drive_id(drive_id).search_with_q(query.strip()),
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_item(i) for i in items]

    # ---- items: write -------------------------------------------------------

    async def upload_file(
        self,
        drive_id: str,
        *,
        name: str,
        content: bytes,
        parent_item_id: str | None = None,
        parent_path: str | None = None,
        conflict: str = "replace",
    ) -> DriveItem:
        """Create or replace a small file in one request.

        Args:
            drive_id: The library.
            name: The file name -- one path segment.
            content: The bytes; at most :data:`MAX_UPLOAD_BYTES` (Graph's
                simple-upload limit; larger files need an upload session).
            parent_item_id: The folder to upload into, by item id.
            parent_path: The folder to upload into, by drive-relative path.
                Mutually exclusive with ``parent_item_id``; neither means the
                drive root.
            conflict: What to do if a file of that name exists --
                ``"replace"`` (default), ``"rename"`` or ``"fail"``.

        Raises:
            ValueError: Bad arguments or oversized content.
            GraphConflictError: ``conflict="fail"`` and the name is taken.
            GraphNotFoundError: The parent does not exist or is not visible.
        """
        _check_id(drive_id, "drive_id")
        clean_name = _check_name(name)
        behaviour = _check_conflict(conflict)
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"content is {len(content)} bytes; files over {MAX_UPLOAD_BYTES} bytes need "
                "an upload session"
            )
        clean_path = _clean_path(parent_path)
        if clean_path and parent_item_id:
            raise ValueError("pass either parent_item_id or parent_path, not both")

        base = f"{self._base_url}/drives/{drive_id}"
        if clean_path:
            target = f"{base}/root:/{clean_path}/{quote(clean_name)}:/content"
        else:
            parent = parent_item_id.strip() if parent_item_id else "root"
            target = f"{base}/items/{quote(parent, safe='')}:/{quote(clean_name)}:/content"
        url = f"{target}?{_CONFLICT_KEY}={behaviour}"

        # The content builder's ``put`` is the simple-upload call; ``with_url``
        # redirects it at the path-addressed target while keeping its parser.
        builder = (
            self.graph.drives.by_drive_id(drive_id)
            .items.by_drive_item_id("root")
            .content.with_url(url)
        )
        async with translate_graph_errors():
            created = await builder.put(bytes(content))
        if created is None:
            raise GraphError("Graph returned no body for the uploaded file")
        return _item(created)

    async def create_folder(
        self,
        drive_id: str,
        name: str,
        *,
        parent_item_id: str | None = None,
        parent_path: str | None = None,
        conflict: str = "fail",
    ) -> DriveItem:
        """Create a folder under the drive root, a folder id, or a folder path.

        Args:
            conflict: ``"fail"`` (default) raises ``GraphConflictError`` when
                the name is taken; ``"rename"`` appends a counter;
                ``"replace"`` is accepted by Graph but rarely what is meant
                for folders.

        Raises:
            ValueError: Bad arguments.
            GraphNotFoundError: The parent does not exist or is not visible.
        """
        _check_id(drive_id, "drive_id")
        clean_name = _check_name(name)
        behaviour = _check_conflict(conflict)
        clean_path = _clean_path(parent_path)
        if clean_path and parent_item_id:
            raise ValueError("pass either parent_item_id or parent_path, not both")

        parent = parent_item_id.strip() if parent_item_id else "root"
        if clean_path:
            # Resolve the path to an id first: child builders hung off a
            # ``with_url`` builder inherit its raw URL rather than appending.
            parent = (await self.get_item_by_path(drive_id, parent_path or "")).id

        body = GraphDriveItem(
            name=clean_name,
            folder=Folder(),
            additional_data={_CONFLICT_KEY: behaviour},
        )
        async with translate_graph_errors():
            created = (
                await self.graph.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(parent)
                .children.post(body)
            )
        if created is None:
            raise GraphError("Graph returned no body for the created folder")
        return _item(created)

    async def move_item(
        self,
        drive_id: str,
        item_id: str,
        *,
        new_parent_id: str | None = None,
        new_name: str | None = None,
    ) -> DriveItem:
        """Move an item to another folder, rename it, or both.

        Args:
            new_parent_id: The destination folder's item id (from a listing;
                ``DriveItem.parent_id`` gives the current one).
            new_name: The new file or folder name.

        Raises:
            ValueError: Neither change requested, or a bad name.
            GraphNotFoundError: The item or destination is not visible.
            GraphConflictError: The destination already has that name.
        """
        _check_id(drive_id, "drive_id")
        _check_id(item_id, "item_id")
        if not new_parent_id and not new_name:
            raise ValueError("nothing to do: pass new_parent_id and/or new_name")
        patch = GraphDriveItem()
        if new_name:
            patch.name = _check_name(new_name)
        if new_parent_id:
            patch.parent_reference = ItemReference(id=new_parent_id.strip())

        async with translate_graph_errors():
            updated = (
                await self.graph.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(item_id)
                .patch(patch)
            )
        if updated is None:
            raise GraphError("Graph returned no body for the updated item")
        return _item(updated)

    async def delete_item(self, drive_id: str, item_id: str) -> None:
        """Delete a file or folder.

        Graph moves it to the site's recycle bin, from which a site owner can
        restore it -- this is not a hard delete.

        Raises:
            ValueError: An id is empty.
            GraphNotFoundError: The item does not exist or is not visible.
        """
        _check_id(drive_id, "drive_id")
        _check_id(item_id, "item_id")
        async with translate_graph_errors():
            await self.graph.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).delete()

    # ---- lists ------------------------------------------------------------

    async def list_lists(self, site_id: str, *, top: int = DEFAULT_TOP) -> list[SharePointList]:
        """The lists on a site -- including document libraries, which Graph
        models as lists too (``template == "documentLibrary"``).

        Raises:
            ValueError: ``site_id`` is empty or ``top`` is out of range.
            GraphNotFoundError: The site does not exist or is not visible.
        """
        _check_id(site_id, "site_id")
        check_top(top)
        params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters(
            top=top, select=_LIST_SELECT
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            items = await collect(
                self.graph.sites.by_site_id(site_id).lists,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_list(v) for v in items]

    async def get_list_items(
        self, site_id: str, list_id: str, *, top: int = DEFAULT_TOP
    ) -> list[ListItemRecord]:
        """Rows of a list, each with its column values in ``fields``.

        Raises:
            ValueError: An id is empty or ``top`` is out of range.
            GraphNotFoundError: The site or list does not exist or is not
                visible.
        """
        _check_id(site_id, "site_id")
        _check_id(list_id, "list_id")
        check_top(top)
        params = ListItemsRequestBuilder.ItemsRequestBuilderGetQueryParameters(
            top=top, expand=["fields"]
        )
        config = RequestConfiguration(query_parameters=params)

        async with translate_graph_errors():
            items = await collect(
                self.graph.sites.by_site_id(site_id).lists.by_list_id(list_id).items,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_list_item(v) for v in items]
