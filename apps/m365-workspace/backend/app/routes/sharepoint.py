"""SharePoint routes — the HTTP face of ``sharepoint-client``.

Same shape as the Outlook router: one SDK call per handler, identity and
error mapping from ``app/graph.py``, argument checks from ``app/main.py``.
Site ids contain commas and drive-item ids contain ``!`` and ``.``; both
travel as ordinary path segments (the SPA ``encodeURIComponent``s them).

Deletes go to the site recycle bin — the SDK has no permanent delete, and
neither does this API.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from msgraph import GraphServiceClient
from pydantic import BaseModel, Field
from sharepoint_client import (
    DEFAULT_TOP,
    MAX_TOP,
    Drive,
    DriveItem,
    ListItemRecord,
    SharePointClient,
    SharePointList,
    Site,
)

from app.graph import get_graph
from app.routes.outlook import content_disposition

router = APIRouter(prefix="/v1/sharepoint", tags=["sharepoint"])

Graph = Annotated[GraphServiceClient, Depends(get_graph)]
Top = Annotated[int, Query(ge=1, le=MAX_TOP)]
Conflict = Literal["fail", "replace", "rename"]


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_item_id: str | None = None
    parent_path: str | None = Field(default=None, max_length=1024)
    conflict: Conflict = "fail"


class MoveItemRequest(BaseModel):
    new_parent_id: str | None = None
    new_name: str | None = Field(default=None, max_length=255)


# ── Sites and lists ──────────────────────────────────────────────────


@router.get("/sites", response_model=list[Site])
async def sites(
    graph: Graph,
    q: Annotated[str | None, Query(max_length=200)] = None,
    top: Top = DEFAULT_TOP,
) -> list[Site]:
    """Sites the caller can see; blank ``q`` lists recently active ones."""
    return await SharePointClient(graph).search_sites(q, top=top)


@router.get("/sites/{site_id}", response_model=Site)
async def site(graph: Graph, site_id: str) -> Site:
    return await SharePointClient(graph).get_site(site_id)


@router.get("/sites/{site_id}/drives", response_model=list[Drive])
async def drives(graph: Graph, site_id: str, top: Top = DEFAULT_TOP) -> list[Drive]:
    return await SharePointClient(graph).list_drives(site_id, top=top)


@router.get("/sites/{site_id}/lists", response_model=list[SharePointList])
async def lists(graph: Graph, site_id: str, top: Top = MAX_TOP) -> list[SharePointList]:
    return await SharePointClient(graph).list_lists(site_id, top=top)


@router.get("/sites/{site_id}/lists/{list_id}/items", response_model=list[ListItemRecord])
async def list_items(
    graph: Graph, site_id: str, list_id: str, top: Top = MAX_TOP
) -> list[ListItemRecord]:
    return await SharePointClient(graph).get_list_items(site_id, list_id, top=top)


# ── Files and folders ────────────────────────────────────────────────


@router.get("/drives/{drive_id}/items", response_model=list[DriveItem])
async def items(
    graph: Graph,
    drive_id: str,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    item_id: str | None = None,
    top: Top = MAX_TOP,
) -> list[DriveItem]:
    """Children of the root, of a folder by ``path``, or of a folder by ``item_id``."""
    return await SharePointClient(graph).list_items(drive_id, path=path, item_id=item_id, top=top)


@router.get("/drives/{drive_id}/item-by-path", response_model=DriveItem)
async def item_by_path(
    graph: Graph, drive_id: str, path: Annotated[str, Query(min_length=1, max_length=1024)]
) -> DriveItem:
    return await SharePointClient(graph).get_item_by_path(drive_id, path)


@router.get("/drives/{drive_id}/search", response_model=list[DriveItem])
async def search(
    graph: Graph,
    drive_id: str,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    top: Top = DEFAULT_TOP,
) -> list[DriveItem]:
    return await SharePointClient(graph).search_files(drive_id, q, top=top)


@router.post("/drives/{drive_id}/upload", status_code=201, response_model=DriveItem)
async def upload(
    graph: Graph,
    drive_id: str,
    file: Annotated[UploadFile, File()],
    parent_item_id: Annotated[str | None, Form()] = None,
    parent_path: Annotated[str | None, Form(max_length=1024)] = None,
    conflict: Annotated[Conflict, Form()] = "replace",
) -> DriveItem:
    """Upload one file (Graph's 4 MB single-request limit applies)."""
    data = await file.read()
    return await SharePointClient(graph).upload_file(
        drive_id,
        name=file.filename or "upload",
        content=data,
        parent_item_id=parent_item_id or None,
        parent_path=parent_path or None,
        conflict=conflict,
    )


@router.post("/drives/{drive_id}/folders", status_code=201, response_model=DriveItem)
async def create_folder(graph: Graph, drive_id: str, body: CreateFolderRequest) -> DriveItem:
    return await SharePointClient(graph).create_folder(
        drive_id,
        body.name,
        parent_item_id=body.parent_item_id,
        parent_path=body.parent_path,
        conflict=body.conflict,
    )


@router.get("/drives/{drive_id}/items/{item_id}", response_model=DriveItem)
async def item(graph: Graph, drive_id: str, item_id: str) -> DriveItem:
    return await SharePointClient(graph).get_item(drive_id, item_id)


@router.patch("/drives/{drive_id}/items/{item_id}", response_model=DriveItem)
async def move_item(graph: Graph, drive_id: str, item_id: str, body: MoveItemRequest) -> DriveItem:
    """Move (``new_parent_id``), rename (``new_name``), or both."""
    return await SharePointClient(graph).move_item(
        drive_id, item_id, new_parent_id=body.new_parent_id, new_name=body.new_name
    )


@router.delete("/drives/{drive_id}/items/{item_id}", status_code=204, response_class=Response)
async def delete_item(graph: Graph, drive_id: str, item_id: str) -> Response:
    await SharePointClient(graph).delete_item(drive_id, item_id)
    return Response(status_code=204)


@router.get(
    "/drives/{drive_id}/items/{item_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def item_content(graph: Graph, drive_id: str, item_id: str) -> Response:
    """The file's bytes, typed from its metadata so the browser can render or save it."""
    sp = SharePointClient(graph)
    meta = await sp.get_item(drive_id, item_id)
    data = await sp.download_item(drive_id, item_id)
    return Response(
        content=data,
        media_type=meta.mime_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(meta.name or "file")},
    )
