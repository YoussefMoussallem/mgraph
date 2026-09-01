"""Typed results for the SharePoint client.

Frozen dataclasses rather than pydantic, matching ``m365_client`` and
``outlook_client``: the Graph SDK already pulls in a dozen packages and none
of them need pydantic. They serialise through ``dataclasses.asdict`` and are
accepted directly as FastAPI ``response_model`` types.

Every field except ``id`` is optional. Graph returns what ``$select`` asks
for, and tenants differ in what they populate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["Drive", "DriveItem", "Identity", "ListItemRecord", "SharePointList", "Site"]


@dataclass(frozen=True)
class Identity:
    """Who created, modified, or owns something.

    ``email`` is best-effort. The generated Graph ``Identity`` model declares
    only ``display_name`` and ``id``, yet real responses for ``createdBy``,
    ``lastModifiedBy`` and a drive's ``owner.user`` usually carry an email;
    the client reads it out of Kiota's ``additional_data`` so it is not
    silently dropped.
    """

    display_name: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class Site:
    """One SharePoint site.

    Attributes:
        id: The composite Graph site id, e.g.
            ``contoso.sharepoint.com,<site-guid>,<web-guid>``. It contains
            commas and must be treated as opaque; it is exactly what
            :meth:`~sharepoint_client.SharePointClient.get_site` accepts.
    """

    id: str
    display_name: str | None = None
    name: str | None = None
    web_url: str | None = None
    created_at: datetime | None = None
    last_modified_at: datetime | None = None


@dataclass(frozen=True)
class Drive:
    """A document library.

    Attributes:
        drive_type: ``"documentLibrary"`` for SharePoint libraries.
        quota_used / quota_total: Bytes, when the tenant reports them.
    """

    id: str
    name: str | None = None
    drive_type: str | None = None
    web_url: str | None = None
    created_at: datetime | None = None
    last_modified_at: datetime | None = None
    owner: Identity | None = None
    quota_used: int | None = None
    quota_total: int | None = None


@dataclass(frozen=True)
class DriveItem:
    """A file or folder inside a drive.

    Attributes:
        is_folder: ``True`` when Graph returned a ``folder`` facet. Folders
            have a ``child_count`` and no ``mime_type``; files the reverse.
        id: Opaque. Drive item ids contain ``!`` and ``.`` and must be
            URL-encoded when placed in a path.
        parent_id: The containing folder's item id, when Graph reports it --
            what :meth:`~sharepoint_client.SharePointClient.move_item` takes.
    """

    id: str
    name: str | None = None
    size: int | None = None
    created_at: datetime | None = None
    last_modified_at: datetime | None = None
    web_url: str | None = None
    mime_type: str | None = None
    is_folder: bool = False
    child_count: int | None = None
    created_by: Identity | None = None
    last_modified_by: Identity | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class SharePointList:
    """A SharePoint list (the structured-data kind, not a document library).

    Attributes:
        template: Graph's list template name, e.g. ``"genericList"``,
            ``"documentLibrary"``, ``"tasks"``.
        hidden: System lists Graph marks hidden; usually not what a user
            means by "the lists on this site".
    """

    id: str
    name: str | None = None
    display_name: str | None = None
    web_url: str | None = None
    description: str | None = None
    template: str | None = None
    hidden: bool | None = None


@dataclass(frozen=True)
class ListItemRecord:
    """One row of a SharePoint list.

    ``fields`` is the row's column values keyed by internal column name,
    exactly as Graph returns them -- lists are user-defined, so there is no
    fixed schema to type against.
    """

    id: str
    web_url: str | None = None
    created_at: datetime | None = None
    last_modified_at: datetime | None = None
    fields: dict[str, Any] = field(default_factory=dict)
