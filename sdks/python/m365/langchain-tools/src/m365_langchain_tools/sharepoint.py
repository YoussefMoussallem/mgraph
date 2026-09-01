"""SharePoint tools: site search, library, folder and file operations, lists.

Thin adapters over :class:`sharepoint_client.SharePointClient`. The chain the
model is expected to walk — sites → drives → files → file content — mirrors
Microsoft Graph's own shape, and each description tells the model where its
IDs come from, because SharePoint IDs are opaque (site IDs contain commas,
item IDs contain ``!`` and ``.``) and cannot be invented.

Every argument is re-validated inside ``_arun()``: some hosts (Apex among
them) call ``_arun`` directly rather than going through ``ainvoke()``, so
Pydantic constraints on the schema alone are not guaranteed to have run.

Write tools say so in their descriptions and go out as the signed-in user —
an uploaded file shows that user as its author. Deletion goes to the site
recycle bin, never a hard delete.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from sharepoint_client import DEFAULT_TOP, MAX_TOP, MAX_UPLOAD_BYTES, SharePointClient

from m365_langchain_tools._common import (
    MAX_TEXT_CHARS,
    RECOVERABLE_ERRORS,
    GraphProvider,
    M365BaseTool,
    dump_json,
    is_text_mime,
    recoverable_error_text,
)

__all__ = [
    "CreateSharePointFolderTool",
    "DeleteSharePointItemTool",
    "GetSharePointListItemsTool",
    "ListSharePointDrivesTool",
    "ListSharePointFilesTool",
    "ListSharePointListsTool",
    "MoveSharePointItemTool",
    "ReadSharePointFileTool",
    "SearchSharePointFilesTool",
    "SearchSharePointSitesTool",
    "UploadSharePointFileTool",
    "sharepoint_tools",
]

_TOP_DESCRIPTION = f"How many items to return, 1-{MAX_TOP}."
_DRIVE_ID_DESCRIPTION = "A drive ID exactly as returned by list_sharepoint_drives."
_SITE_ID_DESCRIPTION = "A site ID exactly as returned by search_sharepoint_sites."
_MAX_DOWNLOAD_BYTES = 5_000_000


def _drive_id() -> Any:
    return Field(min_length=1, max_length=512, description=_DRIVE_ID_DESCRIPTION)


def _site_id() -> Any:
    return Field(min_length=1, max_length=512, description=_SITE_ID_DESCRIPTION)


def _top(default: int = DEFAULT_TOP) -> Any:
    return Field(default=default, ge=1, le=MAX_TOP, description=_TOP_DESCRIPTION)


# ═════════════════════════════════════════════════════════════════════
# Sites and drives
# ═════════════════════════════════════════════════════════════════════


class SearchSharePointSitesInput(BaseModel):
    query: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Search text matched against site names. Omit to list the sites the user has "
            "recently been active in."
        ),
    )
    top: int = _top()


class SearchSharePointSitesTool(M365BaseTool):
    name: str = "search_sharepoint_sites"
    description: str = (
        "Find SharePoint sites the signed-in user can access — the entry point for any "
        "SharePoint task. Returns site IDs and names; pass a site 'id' (copied exactly — "
        "they contain commas) to list_sharepoint_drives or list_sharepoint_lists next. "
        "Only sites this user can already see are returned. Not for the user's own email. "
        "Read-only."
    )
    args_schema: type[BaseModel] = SearchSharePointSitesInput

    async def _arun(self, query: str | None = None, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = SearchSharePointSitesInput(query=query, top=top)
        graph = await self._graph()
        try:
            sites = await SharePointClient(graph).search_sites(args.query, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not sites:
            hint = f" matching '{args.query}'" if args.query else ""
            return (
                f"No SharePoint sites{hint} are visible to the signed-in user. "
                "Try a shorter or different search term."
            )
        return dump_json({"count": len(sites), "sites": [asdict(s) for s in sites]})


class ListSharePointDrivesInput(BaseModel):
    site_id: str = _site_id()
    top: int = _top()


class ListSharePointDrivesTool(M365BaseTool):
    name: str = "list_sharepoint_drives"
    description: str = (
        "List a SharePoint site's document libraries (drives). Requires a site ID from "
        "search_sharepoint_sites. Returns drive IDs and names; pass a drive 'id' to "
        "list_sharepoint_files or search_sharepoint_files next. Most sites have one "
        "library named 'Documents'. Read-only."
    )
    args_schema: type[BaseModel] = ListSharePointDrivesInput

    async def _arun(self, site_id: str, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = ListSharePointDrivesInput(site_id=site_id, top=top)
        graph = await self._graph()
        try:
            drives = await SharePointClient(graph).list_drives(args.site_id, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not drives:
            return "This site has no document libraries."
        return dump_json({"count": len(drives), "drives": [asdict(d) for d in drives]})


# ═════════════════════════════════════════════════════════════════════
# Files — read
# ═════════════════════════════════════════════════════════════════════


class ListSharePointFilesInput(BaseModel):
    drive_id: str = _drive_id()
    path: str | None = Field(
        default=None,
        max_length=1024,
        description=(
            "A folder path relative to the drive root, e.g. 'Reports/2026'. Build it from "
            "folder names seen in earlier listings. Omit both path and item_id for the "
            "drive root."
        ),
    )
    item_id: str | None = Field(
        default=None,
        max_length=512,
        description="A folder's item ID from an earlier listing (alternative to path).",
    )
    top: int = _top()


class ListSharePointFilesTool(M365BaseTool):
    name: str = "list_sharepoint_files"
    description: str = (
        "List the files and folders inside a SharePoint document library or one of its "
        "folders. Requires a drive ID from list_sharepoint_drives; pick the folder with "
        "'path' or 'item_id', or omit both for the root. Entries with is_folder=true can "
        "be listed deeper; files can be read with read_sharepoint_file. Each entry carries "
        "its parent_id, which move_sharepoint_item accepts as a destination. Read-only."
    )
    args_schema: type[BaseModel] = ListSharePointFilesInput

    async def _arun(
        self,
        drive_id: str,
        path: str | None = None,
        item_id: str | None = None,
        top: int = DEFAULT_TOP,
        **_: Any,
    ) -> str:
        args = ListSharePointFilesInput(drive_id=drive_id, path=path, item_id=item_id, top=top)
        graph = await self._graph()
        try:
            items = await SharePointClient(graph).list_items(
                args.drive_id, path=args.path, item_id=args.item_id, top=args.top
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not items:
            where = f"folder '{args.path or args.item_id}'" if (args.path or args.item_id) else "root"
            return f"The {where} of this library is empty."
        return dump_json({"count": len(items), "items": [asdict(i) for i in items]})


class SearchSharePointFilesInput(BaseModel):
    drive_id: str = _drive_id()
    query: str = Field(
        min_length=1,
        max_length=256,
        description="Words to match in file names or content, e.g. 'budget 2026'.",
    )
    top: int = _top()


class SearchSharePointFilesTool(M365BaseTool):
    name: str = "search_sharepoint_files"
    description: str = (
        "Search a SharePoint document library for files by name or content, across every "
        "folder, ranked by relevance. Requires a drive ID from list_sharepoint_drives. Use "
        "this instead of listing folder by folder when the user names a document but not "
        "where it lives. Read-only."
    )
    args_schema: type[BaseModel] = SearchSharePointFilesInput

    async def _arun(self, drive_id: str, query: str, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = SearchSharePointFilesInput(drive_id=drive_id, query=query, top=top)
        graph = await self._graph()
        try:
            items = await SharePointClient(graph).search_files(args.drive_id, args.query, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not items:
            return f"No files matching '{args.query}' in this library."
        return dump_json({"count": len(items), "items": [asdict(i) for i in items]})


class ReadSharePointFileInput(BaseModel):
    drive_id: str = Field(min_length=1, max_length=512, description="The drive ID the file lives in.")
    item_id: str = Field(
        min_length=1,
        max_length=512,
        description="The file's item ID exactly as returned by a listing or search.",
    )
    max_chars: int = Field(
        default=20_000,
        ge=200,
        le=MAX_TEXT_CHARS,
        description="Cap on the returned text length; longer files are truncated.",
    )


class ReadSharePointFileTool(M365BaseTool):
    name: str = "read_sharepoint_file"
    description: str = (
        "Read the text content of a file in a SharePoint document library — plain text, "
        "Markdown, CSV, JSON, XML, YAML, HTML, source code. Requires the drive ID and the "
        "file's item ID from list_sharepoint_files or search_sharepoint_files. Binary "
        "formats (docx, xlsx, pptx, pdf, images) cannot be read by this tool and are "
        "refused with the file's type. Read-only, and slower than a listing."
    )
    args_schema: type[BaseModel] = ReadSharePointFileInput

    async def _arun(self, drive_id: str, item_id: str, max_chars: int = 20_000, **_: Any) -> str:
        args = ReadSharePointFileInput(drive_id=drive_id, item_id=item_id, max_chars=max_chars)
        graph = await self._graph()
        sp = SharePointClient(graph)
        try:
            item = await sp.get_item(args.drive_id, args.item_id)
            if item.is_folder:
                return (
                    f"'{item.name}' is a folder, not a file. Use list_sharepoint_files with "
                    f"item_id='{item.id}' to list what is inside it."
                )
            if not is_text_mime(item.mime_type):
                return (
                    f"'{item.name}' has type '{item.mime_type or 'unknown'}', which this tool "
                    "cannot render as text. Only text-based formats are readable; report the "
                    "file's name and web_url to the user instead of retrying."
                )
            if item.size and item.size > _MAX_DOWNLOAD_BYTES:
                return (
                    f"'{item.name}' is {item.size} bytes, which exceeds the "
                    f"{_MAX_DOWNLOAD_BYTES}-byte read limit. Report its name and web_url "
                    "to the user instead of retrying."
                )
            content = await sp.download_item(args.drive_id, args.item_id)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        text = content.decode("utf-8", errors="replace")
        return dump_json(
            {
                "name": item.name,
                "mime_type": item.mime_type,
                "size_bytes": item.size,
                "web_url": item.web_url,
                "truncated": len(text) > args.max_chars,
                "text": text[: args.max_chars],
            }
        )


# ═════════════════════════════════════════════════════════════════════
# Files — write
# ═════════════════════════════════════════════════════════════════════

_PARENT_PATH_DESCRIPTION = (
    "Destination folder as a drive-relative path, e.g. 'Reports/2026'. Omit both "
    "parent_path and parent_item_id for the drive root."
)
_PARENT_ID_DESCRIPTION = "Destination folder's item ID from a listing (alternative to parent_path)."


class UploadSharePointFileInput(BaseModel):
    drive_id: str = _drive_id()
    name: str = Field(
        min_length=1,
        max_length=255,
        description="File name including extension, e.g. 'summary.md'. No slashes.",
    )
    content: str = Field(
        min_length=1,
        max_length=MAX_TEXT_CHARS,
        description="The file's text content, written as UTF-8.",
    )
    parent_path: str | None = Field(default=None, max_length=1024, description=_PARENT_PATH_DESCRIPTION)
    parent_item_id: str | None = Field(default=None, max_length=512, description=_PARENT_ID_DESCRIPTION)
    conflict: str = Field(
        default="rename",
        pattern="^(fail|replace|rename)$",
        description=(
            "If a file with that name exists: 'rename' (default, keeps both), 'replace' "
            "(overwrites — only when the user asked to update that file), or 'fail'."
        ),
    )


class UploadSharePointFileTool(M365BaseTool):
    name: str = "upload_sharepoint_file"
    description: str = (
        "Create a text file (Markdown, CSV, JSON, plain text, ...) in a SharePoint document "
        "library, authored as the signed-in user. SIDE EFFECT: writes to the library; with "
        "conflict='replace' it overwrites an existing file of the same name. Requires a "
        "drive ID from list_sharepoint_drives. Returns the created item's ID and web link. "
        "Cannot produce Office formats (docx, xlsx) — write .md or .csv instead."
    )
    args_schema: type[BaseModel] = UploadSharePointFileInput

    async def _arun(
        self,
        drive_id: str,
        name: str,
        content: str,
        parent_path: str | None = None,
        parent_item_id: str | None = None,
        conflict: str = "rename",
        **_: Any,
    ) -> str:
        args = UploadSharePointFileInput(
            drive_id=drive_id,
            name=name,
            content=content,
            parent_path=parent_path,
            parent_item_id=parent_item_id,
            conflict=conflict,
        )
        data = args.content.encode("utf-8")
        if len(data) > MAX_UPLOAD_BYTES:
            return (
                f"Error: the content is {len(data)} bytes, over the {MAX_UPLOAD_BYTES}-byte "
                "single-upload limit. Split it into smaller files."
            )
        graph = await self._graph()
        try:
            item = await SharePointClient(graph).upload_file(
                args.drive_id,
                name=args.name,
                content=data,
                parent_item_id=args.parent_item_id,
                parent_path=args.parent_path,
                conflict=args.conflict,
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"uploaded": True, **asdict(item)})


class CreateSharePointFolderInput(BaseModel):
    drive_id: str = _drive_id()
    name: str = Field(min_length=1, max_length=255, description="The folder name. No slashes.")
    parent_path: str | None = Field(default=None, max_length=1024, description=_PARENT_PATH_DESCRIPTION)
    parent_item_id: str | None = Field(default=None, max_length=512, description=_PARENT_ID_DESCRIPTION)


class CreateSharePointFolderTool(M365BaseTool):
    name: str = "create_sharepoint_folder"
    description: str = (
        "Create a folder in a SharePoint document library, under the root or inside an "
        "existing folder given by path or item ID. SIDE EFFECT: writes to the library. "
        "Fails, rather than duplicating, if a folder of that name already exists there. "
        "Returns the new folder's ID for use as a parent in later calls."
    )
    args_schema: type[BaseModel] = CreateSharePointFolderInput

    async def _arun(
        self,
        drive_id: str,
        name: str,
        parent_path: str | None = None,
        parent_item_id: str | None = None,
        **_: Any,
    ) -> str:
        args = CreateSharePointFolderInput(
            drive_id=drive_id, name=name, parent_path=parent_path, parent_item_id=parent_item_id
        )
        graph = await self._graph()
        try:
            folder = await SharePointClient(graph).create_folder(
                args.drive_id,
                args.name,
                parent_item_id=args.parent_item_id,
                parent_path=args.parent_path,
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"created": True, **asdict(folder)})


class MoveSharePointItemInput(BaseModel):
    drive_id: str = _drive_id()
    item_id: str = Field(min_length=1, max_length=512, description="The file or folder to move or rename.")
    new_parent_id: str | None = Field(
        default=None,
        max_length=512,
        description="Destination folder's item ID (a listing's parent_id or a folder's id).",
    )
    new_name: str | None = Field(default=None, max_length=255, description="New file or folder name. No slashes.")


class MoveSharePointItemTool(M365BaseTool):
    name: str = "move_sharepoint_item"
    description: str = (
        "Move a file or folder to another folder in the same library, rename it, or both. "
        "SIDE EFFECT: changes the library; links to the old location may break. Requires "
        "the item's ID and, for a move, the destination folder's item ID from a listing. "
        "Fails if the destination already has that name."
    )
    args_schema: type[BaseModel] = MoveSharePointItemInput

    async def _arun(
        self,
        drive_id: str,
        item_id: str,
        new_parent_id: str | None = None,
        new_name: str | None = None,
        **_: Any,
    ) -> str:
        args = MoveSharePointItemInput(
            drive_id=drive_id, item_id=item_id, new_parent_id=new_parent_id, new_name=new_name
        )
        graph = await self._graph()
        try:
            item = await SharePointClient(graph).move_item(
                args.drive_id, args.item_id, new_parent_id=args.new_parent_id, new_name=args.new_name
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"updated": True, **asdict(item)})


class DeleteSharePointItemInput(BaseModel):
    drive_id: str = _drive_id()
    item_id: str = Field(min_length=1, max_length=512, description="The file or folder to delete.")


class DeleteSharePointItemTool(M365BaseTool):
    name: str = "delete_sharepoint_item"
    description: str = (
        "Delete a file or folder from a SharePoint document library. SIDE EFFECT: the item "
        "moves to the site recycle bin (a site owner can restore it; this tool cannot). "
        "Deleting a folder deletes everything inside it. Confirm with the user before "
        "calling, and never call it on an ID you have not seen in a listing."
    )
    args_schema: type[BaseModel] = DeleteSharePointItemInput

    async def _arun(self, drive_id: str, item_id: str, **_: Any) -> str:
        args = DeleteSharePointItemInput(drive_id=drive_id, item_id=item_id)
        graph = await self._graph()
        try:
            await SharePointClient(graph).delete_item(args.drive_id, args.item_id)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"deleted": True, "item_id": args.item_id, "recoverable_from": "site recycle bin"})


# ═════════════════════════════════════════════════════════════════════
# Lists
# ═════════════════════════════════════════════════════════════════════


class ListSharePointListsInput(BaseModel):
    site_id: str = _site_id()
    top: int = _top()


class ListSharePointListsTool(M365BaseTool):
    name: str = "list_sharepoint_lists"
    description: str = (
        "List the SharePoint lists on a site — task lists, issue trackers, custom tables, "
        "and the document libraries Graph also models as lists (template "
        "'documentLibrary'). Requires a site ID from search_sharepoint_sites. Pass a list "
        "'id' to get_sharepoint_list_items to read its rows. Entries with hidden=true are "
        "system lists. Read-only."
    )
    args_schema: type[BaseModel] = ListSharePointListsInput

    async def _arun(self, site_id: str, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = ListSharePointListsInput(site_id=site_id, top=top)
        graph = await self._graph()
        try:
            lists = await SharePointClient(graph).list_lists(args.site_id, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not lists:
            return "This site has no lists."
        return dump_json({"count": len(lists), "lists": [asdict(v) for v in lists]})


class GetSharePointListItemsInput(BaseModel):
    site_id: str = _site_id()
    list_id: str = Field(min_length=1, max_length=512, description="A list ID from list_sharepoint_lists.")
    top: int = _top()


class GetSharePointListItemsTool(M365BaseTool):
    name: str = "get_sharepoint_list_items"
    description: str = (
        "Read the rows of a SharePoint list with every column value ('fields', keyed by "
        "internal column name). Requires the site ID and a list ID from "
        "list_sharepoint_lists. Use list_sharepoint_files for document libraries instead. "
        "Read-only."
    )
    args_schema: type[BaseModel] = GetSharePointListItemsInput

    async def _arun(self, site_id: str, list_id: str, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = GetSharePointListItemsInput(site_id=site_id, list_id=list_id, top=top)
        graph = await self._graph()
        try:
            rows = await SharePointClient(graph).get_list_items(args.site_id, args.list_id, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not rows:
            return "This list has no items."
        return dump_json({"count": len(rows), "items": [asdict(r) for r in rows]})


# ═════════════════════════════════════════════════════════════════════
# Factory
# ═════════════════════════════════════════════════════════════════════

_READ_TOOLS: tuple[type[M365BaseTool], ...] = (
    SearchSharePointSitesTool,
    ListSharePointDrivesTool,
    ListSharePointFilesTool,
    SearchSharePointFilesTool,
    ReadSharePointFileTool,
    ListSharePointListsTool,
    GetSharePointListItemsTool,
)
_WRITE_TOOLS: tuple[type[M365BaseTool], ...] = (
    UploadSharePointFileTool,
    CreateSharePointFolderTool,
    MoveSharePointItemTool,
    DeleteSharePointItemTool,
)


def sharepoint_tools(graph_provider: GraphProvider, *, include_writes: bool = True) -> list[BaseTool]:
    """Fresh SharePoint tool instances bound to ``graph_provider``.

    Call once per agent execution and hand the result to ``bind_tools``.
    Instances hold execution-specific state (the provider), so never reuse
    them across concurrent executions. ``include_writes=False`` gives a
    read-only agent the seven read tools and nothing that uploads, moves, or
    deletes.
    """
    classes = _READ_TOOLS + (_WRITE_TOOLS if include_writes else ())
    return [cls(graph_provider=graph_provider) for cls in classes]
