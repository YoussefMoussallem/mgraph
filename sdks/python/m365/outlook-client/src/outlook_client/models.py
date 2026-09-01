"""Typed results for the Outlook client.

Frozen dataclasses rather than pydantic, matching ``m365_client``: the Graph
SDK already pulls in a dozen packages and none of them need pydantic, so
adding it here would grow every consumer's dependency surface for no gain.
They serialise cleanly through ``dataclasses.asdict`` and are accepted
directly as FastAPI ``response_model`` types (pydantic wraps standard
dataclasses), so a service can return them from a route unchanged.

Every field except ``id`` is optional. Graph only returns what ``$select``
asks for, and tenants differ in which properties they populate, so a model
that demanded every field would fail on real mailboxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "Attachment",
    "AttachmentContent",
    "Attendee",
    "Contact",
    "Event",
    "MailFolder",
    "MessageDetail",
    "MessageSummary",
    "Recipient",
    "UserProfile",
]


@dataclass(frozen=True)
class Recipient:
    """An email name and address pair."""

    name: str | None = None
    address: str | None = None


# ── Mail ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MessageSummary:
    """One message as it appears in a list view.

    The body is deliberately absent. Fetching bodies for a list is slow and
    wasteful -- the ``$select`` in the client enforces this -- so a list view
    gets ``body_preview`` and a detail call gets the rest.

    Attributes:
        id: Graph message id. Opaque; contains characters that must be
            URL-encoded when placed in a path.
        received_at: Timezone-aware ``datetime``, as Graph returns it.
        has_attachments: Whether the message carries attachments; list them
            with ``list_attachments``.
        web_link: Deep link that opens the message in Outlook on the web.
    """

    id: str
    subject: str | None = None
    from_name: str | None = None
    from_address: str | None = None
    received_at: datetime | None = None
    is_read: bool | None = None
    body_preview: str | None = None
    has_attachments: bool | None = None
    web_link: str | None = None


@dataclass(frozen=True)
class MessageDetail(MessageSummary):
    """A single message including its full body and recipients.

    Extends :class:`MessageSummary`, so anything that accepts a summary
    accepts a detail.

    Attributes:
        body_content_type: The Graph wire value -- ``"text"`` or ``"html"`` --
            never the enum's Python repr.
    """

    body_content: str | None = None
    body_content_type: str | None = None
    to_recipients: tuple[Recipient, ...] = ()
    cc_recipients: tuple[Recipient, ...] = ()


@dataclass(frozen=True)
class MailFolder:
    """One mail folder, with its item counts."""

    id: str
    display_name: str | None = None
    total_item_count: int | None = None
    unread_item_count: int | None = None


@dataclass(frozen=True)
class Attachment:
    """Metadata for one attachment on a message.

    ``content_type`` is the MIME type Graph reports; ``is_inline`` marks
    images embedded in an HTML body rather than files attached to it.
    """

    id: str
    name: str | None = None
    content_type: str | None = None
    size: int | None = None
    is_inline: bool | None = None


@dataclass(frozen=True)
class AttachmentContent:
    """An attachment's metadata together with its bytes."""

    attachment: Attachment
    content: bytes


# ── Calendar ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Attendee:
    """One attendee of an event and their response.

    Attributes:
        type: ``"required"``, ``"optional"`` or ``"resource"``.
        response: Graph's response value -- ``"none"``, ``"organizer"``,
            ``"tentativelyAccepted"``, ``"accepted"``, ``"declined"`` or
            ``"notResponded"``.
    """

    name: str | None = None
    address: str | None = None
    type: str | None = None
    response: str | None = None


@dataclass(frozen=True)
class Event:
    """One calendar event.

    ``start`` and ``end`` are kept exactly as Graph returns them -- an ISO
    8601 string with no offset -- alongside ``time_zone``, because Graph
    reports wall-clock time in a named zone (``"UTC"``, ``"Pacific Standard
    Time"``, ...) and converting that faithfully would need a zone-name
    mapping this package has no business owning.

    Attributes:
        response_status: The signed-in user's own response to the invitation
            (same values as :attr:`Attendee.response`).
    """

    id: str
    subject: str | None = None
    start: str | None = None
    end: str | None = None
    time_zone: str | None = None
    is_all_day: bool | None = None
    is_cancelled: bool | None = None
    location: str | None = None
    organizer: Recipient | None = None
    attendees: tuple[Attendee, ...] = ()
    is_online_meeting: bool | None = None
    online_meeting_url: str | None = None
    web_link: str | None = None
    body_preview: str | None = None
    response_status: str | None = None


# ── Contacts ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Contact:
    """One personal contact."""

    id: str
    display_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    email_addresses: tuple[str, ...] = ()
    company_name: str | None = None
    job_title: str | None = None
    mobile_phone: str | None = None
    business_phones: tuple[str, ...] = ()


# ── Profile ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserProfile:
    """The signed-in user's profile from ``/me``.

    ``id`` is the Entra object id -- the same value as the ``oid`` claim in
    the caller's token. Comparing the two is the cheapest proof that a
    delegated call is acting as the signed-in user rather than as the
    application.
    """

    id: str
    display_name: str | None = None
    mail: str | None = None
    user_principal_name: str | None = None
    job_title: str | None = None
    office_location: str | None = None
