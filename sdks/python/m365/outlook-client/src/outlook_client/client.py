"""Outlook access over Microsoft Graph: mail, attachments, calendar, contacts.

:class:`OutlookClient` wraps a ``GraphServiceClient`` and exposes the mailbox
operations a platform app needs as typed, paged, error-translated calls. It
owns nothing else: no token acquisition, no caching, no HTTP configuration.
All of that is ``m365_client``'s job, and the ``GraphServiceClient`` it hands
back is what this class is built on.

Which identity the calls run as is the caller's decision, made by choosing
which Graph client to pass in. ``M365Client.graph_for_user()`` gives a client
acting as the signed-in user, so Graph enforces that user's own permissions;
that is the intended and default use. This package never reaches for the
app-only path itself.

Every method wraps its Graph call in
:func:`m365_client.translate_graph_errors`, so callers catch the SDK taxonomy
(``GraphNotFoundError``, ``GraphThrottledError``, ...) rather than Kiota
internals, and every list call walks ``@odata.nextLink`` through
:func:`m365_client.collect` up to the requested ``top``.

Graph delegated permissions by operation group:

======================  =======================================
Read mail, folders      ``Mail.Read``
Send, reply, forward    ``Mail.Send``
Drafts, move, delete,
mark read, attachments  ``Mail.ReadWrite``
Calendar reads          ``Calendars.Read``
Calendar writes         ``Calendars.ReadWrite``
Contacts                ``Contacts.Read``
Profile                 ``User.Read``
======================  =======================================
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from kiota_abstractions.base_request_configuration import RequestConfiguration
from m365_client import (
    DEFAULT_TOP,
    MAX_TOP,
    GraphError,
    GraphInvalidRequestError,
    GraphNotFoundError,
    check_top,
    collect,
    translate_graph_errors,
)
from msgraph import GraphServiceClient
from msgraph.generated.models.attendee import Attendee as GraphAttendee
from msgraph.generated.models.attendee_type import AttendeeType
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.event import Event as GraphEvent
from msgraph.generated.models.file_attachment import FileAttachment
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.location import Location
from msgraph.generated.models.message import Message
from msgraph.generated.models.online_meeting_provider_type import (
    OnlineMeetingProviderType,
)
from msgraph.generated.models.recipient import Recipient as GraphRecipient
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from msgraph.generated.users.item.contacts.contacts_request_builder import (
    ContactsRequestBuilder,
)
from msgraph.generated.users.item.events.events_request_builder import (
    EventsRequestBuilder,
)
from msgraph.generated.users.item.events.item.accept.accept_post_request_body import (
    AcceptPostRequestBody,
)
from msgraph.generated.users.item.events.item.decline.decline_post_request_body import (
    DeclinePostRequestBody,
)
from msgraph.generated.users.item.events.item.tentatively_accept.tentatively_accept_post_request_body import (
    TentativelyAcceptPostRequestBody,
)
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder as FolderMessagesRequestBuilder,
)
from msgraph.generated.users.item.mail_folders.mail_folders_request_builder import (
    MailFoldersRequestBuilder,
)
from msgraph.generated.users.item.messages.item.attachments.attachments_request_builder import (
    AttachmentsRequestBuilder,
)
from msgraph.generated.users.item.messages.item.forward.forward_post_request_body import (
    ForwardPostRequestBody,
)
from msgraph.generated.users.item.messages.item.message_item_request_builder import (
    MessageItemRequestBuilder,
)
from msgraph.generated.users.item.messages.item.move.move_post_request_body import (
    MovePostRequestBody,
)
from msgraph.generated.users.item.messages.item.reply.reply_post_request_body import (
    ReplyPostRequestBody,
)
from msgraph.generated.users.item.messages.item.reply_all.reply_all_post_request_body import (
    ReplyAllPostRequestBody,
)
from msgraph.generated.users.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)
from msgraph.generated.users.item.user_item_request_builder import (
    UserItemRequestBuilder,
)

from outlook_client.models import (
    Attachment,
    AttachmentContent,
    Attendee,
    Contact,
    Event,
    MailFolder,
    MessageDetail,
    MessageSummary,
    Recipient,
    UserProfile,
)

# ``MAX_TOP`` and ``DEFAULT_TOP`` are the platform-wide list caps from
# ``m365_client.paging``, re-exported so consumers can bound their own query
# parameters against the same number this client enforces.
__all__ = ["DEFAULT_TOP", "MAX_ATTACHMENT_BYTES", "MAX_TOP", "OutlookClient"]

#: Graph's ceiling for attaching a file in one request; larger files need an
#: upload session, which this client does not implement.
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024

_BODY_TYPES = frozenset({"text", "html"})
_EVENT_RESPONSES = frozenset({"accept", "decline", "tentative"})

# Fields fetched in list view -- never let Graph return full bodies here.
_MESSAGE_LIST_SELECT = [
    "id",
    "subject",
    "from",
    "receivedDateTime",
    "isRead",
    "bodyPreview",
    "hasAttachments",
    "webLink",
]

# Fields fetched for a single message, full body included.
_MESSAGE_DETAIL_SELECT = [
    *_MESSAGE_LIST_SELECT,
    "toRecipients",
    "ccRecipients",
    "body",
]

_FOLDER_SELECT = ["id", "displayName", "totalItemCount", "unreadItemCount"]

_PROFILE_SELECT = ["id", "displayName", "mail", "userPrincipalName", "jobTitle", "officeLocation"]

_ATTACHMENT_SELECT = ["id", "name", "contentType", "size", "isInline"]

_EVENT_SELECT = [
    "id",
    "subject",
    "start",
    "end",
    "isAllDay",
    "isCancelled",
    "location",
    "organizer",
    "attendees",
    "isOnlineMeeting",
    "onlineMeeting",
    "onlineMeetingUrl",
    "webLink",
    "bodyPreview",
    "responseStatus",
]

_CONTACT_SELECT = [
    "id",
    "displayName",
    "givenName",
    "surname",
    "emailAddresses",
    "companyName",
    "jobTitle",
    "mobilePhone",
    "businessPhones",
]

_ORDER_BY_RECEIVED = ["receivedDateTime DESC"]

# Graph requires that a property in ``$orderby`` also appears in ``$filter``,
# first, when both are given on messages -- otherwise it answers
# ``InefficientFilter``. This clause is always true and satisfies that rule,
# so ``isRead eq false`` can be appended after it.
_ORDER_BY_FILTER_PREFIX = "receivedDateTime ge 1970-01-01T00:00:00Z"
_UNREAD_FILTER = "isRead eq false"


# ── Argument checks ──────────────────────────────────────────────────────────


def _check_id(value: str, name: str) -> None:
    """An empty id would silently turn an item request into a collection request."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _check_addresses(addresses: Sequence[str], name: str, *, required: bool) -> list[str]:
    cleaned = [a.strip() for a in addresses if isinstance(a, str) and a.strip()]
    if required and not cleaned:
        raise ValueError(f"{name} needs at least one email address")
    if any("@" not in a for a in cleaned):
        raise ValueError(f"{name} must contain email addresses, got {cleaned!r}")
    return cleaned


def _check_body_type(body_type: str) -> BodyType:
    if body_type not in _BODY_TYPES:
        raise ValueError(f"body_type must be one of {sorted(_BODY_TYPES)}, got {body_type!r}")
    return BodyType(body_type)


def _iso_local(value: datetime | str, time_zone: str) -> str:
    """Wall-clock ISO 8601 text for a ``DateTimeTimeZone``.

    Graph pairs a zone-less local time with a separate zone name. A
    timezone-aware ``datetime`` is therefore only unambiguous in UTC, so it is
    converted to UTC and the caller must have asked for ``"UTC"``.
    """
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("a start/end time is required")
        return value.strip()
    if value.tzinfo is not None:
        if time_zone.upper() != "UTC":
            raise ValueError(
                "a timezone-aware datetime can only be combined with time_zone='UTC'; "
                "pass a naive datetime for any other zone"
            )
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


# ── Mapping from Graph models ────────────────────────────────────────────────


def _recipient(value: Any) -> Recipient | None:
    email = getattr(value, "email_address", None)
    if email is None:
        return None
    return Recipient(name=email.name, address=email.address)


def _recipients(values: Any) -> tuple[Recipient, ...]:
    return tuple(r for r in (_recipient(v) for v in (values or [])) if r is not None)


def _summary(message: Any) -> MessageSummary:
    sender = _recipient(getattr(message, "from_", None))
    return MessageSummary(
        id=message.id or "",
        subject=message.subject,
        from_name=sender.name if sender else None,
        from_address=sender.address if sender else None,
        received_at=message.received_date_time,
        is_read=message.is_read,
        body_preview=message.body_preview,
        has_attachments=message.has_attachments,
        web_link=message.web_link,
    )


def _detail(message: Any) -> MessageDetail:
    summary = _summary(message)
    body = getattr(message, "body", None)
    # ``content_type`` is a Kiota ``BodyType`` enum member. ``.value`` yields
    # the wire string ("text"/"html"); ``str()`` yields "BodyType.Text", which
    # once leaked into an API response.
    content_type = getattr(body, "content_type", None) if body else None
    return MessageDetail(
        **summary.__dict__,
        body_content=body.content if body else None,
        body_content_type=content_type.value if content_type else None,
        to_recipients=_recipients(message.to_recipients),
        cc_recipients=_recipients(message.cc_recipients),
    )


def _folder(folder: Any) -> MailFolder:
    return MailFolder(
        id=folder.id or "",
        display_name=folder.display_name,
        total_item_count=folder.total_item_count,
        unread_item_count=folder.unread_item_count,
    )


def _profile(user: Any) -> UserProfile:
    return UserProfile(
        id=user.id or "",
        display_name=user.display_name,
        mail=user.mail,
        user_principal_name=user.user_principal_name,
        job_title=user.job_title,
        office_location=user.office_location,
    )


def _attachment(attachment: Any) -> Attachment:
    return Attachment(
        id=attachment.id or "",
        name=attachment.name,
        content_type=attachment.content_type,
        size=attachment.size,
        is_inline=attachment.is_inline,
    )


def _enum_value(value: Any) -> str | None:
    return getattr(value, "value", None) if value is not None else None


def _attendee(attendee: Any) -> Attendee:
    recipient = _recipient(attendee) or Recipient()
    status = getattr(attendee, "status", None)
    return Attendee(
        name=recipient.name,
        address=recipient.address,
        type=_enum_value(getattr(attendee, "type", None)),
        response=_enum_value(getattr(status, "response", None)) if status else None,
    )


def _event(event: Any) -> Event:
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    location = getattr(event, "location", None)
    online = getattr(event, "online_meeting", None)
    response_status = getattr(event, "response_status", None)
    return Event(
        id=event.id or "",
        subject=event.subject,
        start=getattr(start, "date_time", None),
        end=getattr(end, "date_time", None),
        time_zone=getattr(start, "time_zone", None) or getattr(end, "time_zone", None),
        is_all_day=event.is_all_day,
        is_cancelled=event.is_cancelled,
        location=getattr(location, "display_name", None),
        organizer=_recipient(getattr(event, "organizer", None)),
        attendees=tuple(_attendee(a) for a in (event.attendees or [])),
        is_online_meeting=event.is_online_meeting,
        online_meeting_url=(getattr(online, "join_url", None) or event.online_meeting_url),
        web_link=event.web_link,
        body_preview=event.body_preview,
        response_status=_enum_value(getattr(response_status, "response", None))
        if response_status
        else None,
    )


def _contact(contact: Any) -> Contact:
    return Contact(
        id=contact.id or "",
        display_name=contact.display_name,
        given_name=contact.given_name,
        surname=contact.surname,
        email_addresses=tuple(
            e.address for e in (contact.email_addresses or []) if getattr(e, "address", None)
        ),
        company_name=contact.company_name,
        job_title=contact.job_title,
        mobile_phone=contact.mobile_phone,
        business_phones=tuple(contact.business_phones or []),
    )


# ── Graph request bodies ─────────────────────────────────────────────────────


def _graph_recipients(addresses: Sequence[str]) -> list[GraphRecipient]:
    return [GraphRecipient(email_address=EmailAddress(address=a)) for a in addresses]


def _graph_message(
    *,
    subject: str,
    body: str,
    body_type: str,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
) -> Message:
    return Message(
        subject=subject,
        body=ItemBody(content_type=_check_body_type(body_type), content=body),
        to_recipients=_graph_recipients(to),
        cc_recipients=_graph_recipients(cc),
        bcc_recipients=_graph_recipients(bcc),
    )


# ── Client ───────────────────────────────────────────────────────────────────


class OutlookClient:
    """Mailbox, calendar and contact calls on behalf of whoever ``graph`` acts as.

    Cheap to construct -- it holds a reference and nothing else -- so build one
    per request from the per-request Graph client::

        graph = await m365.graph_for_user(user.assertion, user.user_id)
        outlook = OutlookClient(graph)
        messages = await outlook.list_messages(top=10, unread_only=True)
        await outlook.reply_message(messages[0].id, "On it.")

    Attributes:
        graph: The underlying ``GraphServiceClient``, public on purpose. This
            class covers the common operations; anything else in the mailbox
            API is one call away on the official client, with the whole typed
            Graph surface intact.
    """

    def __init__(self, graph: GraphServiceClient) -> None:
        self.graph = graph

    # ---- mail: read ---------------------------------------------------------

    async def list_messages(
        self,
        *,
        top: int = DEFAULT_TOP,
        folder: str | None = None,
        unread_only: bool = False,
        search: str | None = None,
    ) -> list[MessageSummary]:
        """The user's messages, newest first, without bodies.

        Args:
            top: Maximum number of messages to return, 1 to :data:`MAX_TOP`.
                A total cap rather than a page size -- Graph pages are
                followed until this many have been collected or the mailbox
                runs out.
            folder: Restrict to one folder, by well-known name (``inbox``,
                ``sentitems``, ...) or by folder id from :meth:`list_folders`.
                ``None`` lists across the whole mailbox.
            unread_only: Return only unread messages.
            search: Full-text search (subject, body, sender). Graph returns
                search hits by relevance and does not allow ``$filter`` or
                ``$orderby`` alongside it, so ``search`` cannot be combined
                with ``unread_only``.

        Raises:
            ValueError: ``top`` is out of range, or ``search`` is combined
                with ``unread_only``.
            GraphNotFoundError: ``folder`` does not exist or is not visible.
            GraphError: Any other Graph failure, translated.
        """
        check_top(top)
        search = search.strip() if search else None
        if search and unread_only:
            raise ValueError("search cannot be combined with unread_only")

        kwargs: dict[str, Any] = {"top": top, "select": _MESSAGE_LIST_SELECT}
        if search:
            # Graph wants the KQL wrapped in double quotes; strip any the
            # caller supplied so they cannot terminate the literal early.
            kwargs["search"] = f'"{search.replace(chr(34), " ")}"'
        else:
            kwargs["orderby"] = _ORDER_BY_RECEIVED
            if unread_only:
                kwargs["filter"] = f"{_ORDER_BY_FILTER_PREFIX} and {_UNREAD_FILTER}"

        query: Any
        if folder:
            builder = self.graph.me.mail_folders.by_mail_folder_id(folder).messages
            query = FolderMessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(**kwargs)
        else:
            builder = self.graph.me.messages
            query = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(**kwargs)
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            items = await collect(
                builder,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_summary(m) for m in items]

    async def get_message(self, message_id: str) -> MessageDetail:
        """One message by id, including its full body and recipients.

        Raises:
            ValueError: ``message_id`` is empty.
            GraphNotFoundError: The message does not exist or is not visible
                to this user.
        """
        _check_id(message_id, "message_id")
        query = MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(
            select=_MESSAGE_DETAIL_SELECT,
        )
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            message = await self.graph.me.messages.by_message_id(message_id).get(
                request_configuration=config
            )
        if message is None:
            raise GraphNotFoundError(f"Message {message_id} not found", status_code=404)
        return _detail(message)

    async def list_folders(self, *, top: int = 20) -> list[MailFolder]:
        """The user's top-level mail folders.

        Raises:
            ValueError: ``top`` is out of range.
        """
        check_top(top)
        query = MailFoldersRequestBuilder.MailFoldersRequestBuilderGetQueryParameters(
            top=top,
            select=_FOLDER_SELECT,
        )
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            items = await collect(
                self.graph.me.mail_folders,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_folder(f) for f in items]

    async def get_profile(self) -> UserProfile:
        """The signed-in user's own profile, from ``/me``.

        ``client.me`` is generated as ``users.by_user_id("me-token-to-replace")``
        and rewritten to ``/me`` by Graph middleware that ``m365_client``
        keeps installed; the request configuration type is therefore the
        one for a single user item.
        """
        query = UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
            select=_PROFILE_SELECT,
        )
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            me = await self.graph.me.get(request_configuration=config)
        if me is None:
            raise GraphError("Graph returned no body for /me")
        return _profile(me)

    # ---- mail: attachments --------------------------------------------------

    async def list_attachments(self, message_id: str, *, top: int = MAX_TOP) -> list[Attachment]:
        """Attachment metadata for a message -- never the bytes.

        Raises:
            ValueError: ``message_id`` is empty or ``top`` is out of range.
            GraphNotFoundError: The message does not exist or is not visible.
        """
        _check_id(message_id, "message_id")
        check_top(top)
        query = AttachmentsRequestBuilder.AttachmentsRequestBuilderGetQueryParameters(
            top=top,
            select=_ATTACHMENT_SELECT,
        )
        config = RequestConfiguration(query_parameters=query)

        async with translate_graph_errors():
            items = await collect(
                self.graph.me.messages.by_message_id(message_id).attachments,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_attachment(a) for a in items]

    async def download_attachment(self, message_id: str, attachment_id: str) -> AttachmentContent:
        """One file attachment's metadata and bytes.

        Graph has three attachment kinds. Only ``fileAttachment`` carries
        bytes; an attached Outlook item or a cloud-file reference is reported
        as an invalid request rather than returned empty.

        Raises:
            ValueError: An id is empty.
            GraphNotFoundError: The message or attachment is not visible.
            GraphInvalidRequestError: The attachment is not a file attachment.
        """
        _check_id(message_id, "message_id")
        _check_id(attachment_id, "attachment_id")

        async with translate_graph_errors():
            attachment = (
                await self.graph.me.messages.by_message_id(message_id)
                .attachments.by_attachment_id(attachment_id)
                .get()
            )
        if attachment is None:
            raise GraphNotFoundError(f"Attachment {attachment_id} not found", status_code=404)
        if not isinstance(attachment, FileAttachment):
            raise GraphInvalidRequestError(
                f"Attachment {attachment_id} is {attachment.odata_type or 'not a file attachment'}"
                " and has no downloadable bytes",
                status_code=400,
            )
        return AttachmentContent(
            attachment=_attachment(attachment), content=attachment.content_bytes or b""
        )

    async def add_attachment(
        self,
        message_id: str,
        *,
        name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> Attachment:
        """Attach a file to a draft (or any message the user owns).

        Raises:
            ValueError: An argument is empty, or ``content`` exceeds
                :data:`MAX_ATTACHMENT_BYTES` -- Graph's single-request limit;
                larger files need an upload session this client does not
                implement.
        """
        _check_id(message_id, "message_id")
        _check_id(name, "name")
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"content is {len(content)} bytes; attachments over {MAX_ATTACHMENT_BYTES} "
                "bytes need an upload session"
            )
        body = FileAttachment(
            odata_type="#microsoft.graph.fileAttachment",
            name=name,
            content_type=content_type,
            content_bytes=bytes(content),
        )
        async with translate_graph_errors():
            created = await self.graph.me.messages.by_message_id(message_id).attachments.post(body)
        if created is None:
            raise GraphError("Graph returned no body when creating the attachment")
        return _attachment(created)

    # ---- mail: write --------------------------------------------------------

    async def send_message(
        self,
        *,
        subject: str,
        body: str,
        to: Sequence[str],
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        body_type: str = "text",
        save_to_sent: bool = True,
    ) -> None:
        """Send a new message immediately (``sendMail``).

        Not idempotent: calling it twice sends two emails. Prefer
        :meth:`create_draft` when a person should review before sending.

        Raises:
            ValueError: No recipient, a malformed address, or a bad
                ``body_type``.
        """
        message = _graph_message(
            subject=subject,
            body=body,
            body_type=body_type,
            to=_check_addresses(to, "to", required=True),
            cc=_check_addresses(cc, "cc", required=False),
            bcc=_check_addresses(bcc, "bcc", required=False),
        )
        request = SendMailPostRequestBody(message=message, save_to_sent_items=save_to_sent)
        async with translate_graph_errors():
            await self.graph.me.send_mail.post(request)

    async def create_draft(
        self,
        *,
        subject: str,
        body: str,
        to: Sequence[str] = (),
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        body_type: str = "text",
    ) -> MessageDetail:
        """Create a draft in the Drafts folder without sending it.

        The returned message carries the id to pass to :meth:`add_attachment`
        or :meth:`send_draft`, and a ``web_link`` that opens it in Outlook.
        """
        message = _graph_message(
            subject=subject,
            body=body,
            body_type=body_type,
            to=_check_addresses(to, "to", required=False),
            cc=_check_addresses(cc, "cc", required=False),
            bcc=_check_addresses(bcc, "bcc", required=False),
        )
        async with translate_graph_errors():
            created = await self.graph.me.messages.post(message)
        if created is None:
            raise GraphError("Graph returned no body when creating the draft")
        return _detail(created)

    async def send_draft(self, message_id: str) -> None:
        """Send an existing draft. Not idempotent once it has left the Drafts folder."""
        _check_id(message_id, "message_id")
        async with translate_graph_errors():
            await self.graph.me.messages.by_message_id(message_id).send.post()

    async def reply_message(self, message_id: str, comment: str, *, reply_all: bool = False) -> None:
        """Reply to a message with ``comment`` prepended to the quoted original.

        Sends immediately, to the original sender or -- with ``reply_all`` --
        to every original recipient. Not idempotent.
        """
        _check_id(message_id, "message_id")
        builder = self.graph.me.messages.by_message_id(message_id)
        async with translate_graph_errors():
            if reply_all:
                await builder.reply_all.post(ReplyAllPostRequestBody(comment=comment))
            else:
                await builder.reply.post(ReplyPostRequestBody(comment=comment))

    async def forward_message(self, message_id: str, *, to: Sequence[str], comment: str = "") -> None:
        """Forward a message to ``to`` with an optional ``comment``. Sends immediately."""
        _check_id(message_id, "message_id")
        recipients = _graph_recipients(_check_addresses(to, "to", required=True))
        async with translate_graph_errors():
            await self.graph.me.messages.by_message_id(message_id).forward.post(
                ForwardPostRequestBody(comment=comment, to_recipients=recipients)
            )

    async def move_message(self, message_id: str, destination_folder: str) -> MessageDetail:
        """Move a message to another folder, by well-known name or folder id.

        Returns the moved message, which has a **new id** -- the old one
        stops resolving. Moving to ``deleteditems`` is the reversible way to
        delete.
        """
        _check_id(message_id, "message_id")
        _check_id(destination_folder, "destination_folder")
        async with translate_graph_errors():
            moved = await self.graph.me.messages.by_message_id(message_id).move.post(
                MovePostRequestBody(destination_id=destination_folder)
            )
        if moved is None:
            raise GraphError("Graph returned no body for the moved message")
        return _detail(moved)

    async def delete_message(self, message_id: str, *, permanent: bool = False) -> None:
        """Delete a message.

        By default this moves it to Deleted Items, which the user can undo.
        ``permanent=True`` issues a hard delete that cannot be undone.
        """
        _check_id(message_id, "message_id")
        if not permanent:
            await self.move_message(message_id, "deleteditems")
            return
        async with translate_graph_errors():
            await self.graph.me.messages.by_message_id(message_id).delete()

    async def set_read(self, message_id: str, *, read: bool = True) -> None:
        """Mark a message read (default) or unread."""
        _check_id(message_id, "message_id")
        async with translate_graph_errors():
            await self.graph.me.messages.by_message_id(message_id).patch(Message(is_read=read))

    # ---- calendar -----------------------------------------------------------

    async def list_events(
        self,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        top: int = DEFAULT_TOP,
    ) -> list[Event]:
        """Events on the user's calendar, ordered by start.

        With both ``start`` and ``end`` this uses Graph's calendar view, which
        expands recurring series into the occurrences inside the window --
        what "what is on my calendar this week" actually means. Without a
        window it lists upcoming events -- those starting from now on. A
        recurring series counts only if the series itself starts in the
        future, so ask with a window for what is really on the calendar.

        Args:
            start / end: ISO 8601 text or ``datetime``; both or neither.
            top: Maximum number of events, 1 to :data:`MAX_TOP`.

        Raises:
            ValueError: ``top`` is out of range, or only one of ``start`` /
                ``end`` was given.
        """
        check_top(top)
        if (start is None) != (end is None):
            raise ValueError("start and end must be given together")

        if start is not None and end is not None:
            view_query = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
                start_date_time=_iso_local(start, "UTC") if isinstance(start, str) else start.isoformat(),
                end_date_time=_iso_local(end, "UTC") if isinstance(end, str) else end.isoformat(),
                top=top,
                select=_EVENT_SELECT,
                orderby=["start/dateTime"],
            )
            view_config = RequestConfiguration(query_parameters=view_query)
            async with translate_graph_errors():
                items = await collect(
                    self.graph.me.calendar_view,
                    lambda b: b.get(request_configuration=view_config),
                    max_items=top,
                )
            return [_event(e) for e in items]

        # No window: upcoming events only. Without a lower bound, ordering by
        # start would hand back the oldest events in the calendar. Graph
        # reports start/dateTime as a UTC string unless a time-zone preference
        # header is sent, so a UTC bound compares correctly.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        query = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
            top=top,
            select=_EVENT_SELECT,
            filter=f"start/dateTime ge '{now}'",
            orderby=["start/dateTime"],
        )
        config = RequestConfiguration(query_parameters=query)
        async with translate_graph_errors():
            items = await collect(
                self.graph.me.events,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_event(e) for e in items]

    async def get_event(self, event_id: str) -> Event:
        """One event by id."""
        _check_id(event_id, "event_id")
        async with translate_graph_errors():
            event = await self.graph.me.events.by_event_id(event_id).get()
        if event is None:
            raise GraphNotFoundError(f"Event {event_id} not found", status_code=404)
        return _event(event)

    async def create_event(
        self,
        *,
        subject: str,
        start: datetime | str,
        end: datetime | str,
        time_zone: str = "UTC",
        body: str | None = None,
        body_type: str = "text",
        attendees: Sequence[str] = (),
        location: str | None = None,
        is_all_day: bool = False,
        online_meeting: bool = False,
    ) -> Event:
        """Create an event on the user's calendar.

        Attendees receive invitations as soon as the event is created; with
        ``online_meeting`` a Teams meeting link is generated. Times are
        wall-clock values in ``time_zone`` (a Windows or IANA zone name).

        Raises:
            ValueError: Empty subject, malformed attendee address, bad
                ``body_type``, or an aware ``datetime`` with a non-UTC zone.
        """
        _check_id(subject, "subject")
        event = GraphEvent(
            subject=subject,
            start=DateTimeTimeZone(date_time=_iso_local(start, time_zone), time_zone=time_zone),
            end=DateTimeTimeZone(date_time=_iso_local(end, time_zone), time_zone=time_zone),
            is_all_day=is_all_day,
            attendees=[
                GraphAttendee(
                    email_address=EmailAddress(address=a), type=AttendeeType.Required
                )
                for a in _check_addresses(attendees, "attendees", required=False)
            ],
        )
        if body is not None:
            event.body = ItemBody(content_type=_check_body_type(body_type), content=body)
        if location:
            event.location = Location(display_name=location)
        if online_meeting:
            event.is_online_meeting = True
            event.online_meeting_provider = OnlineMeetingProviderType.TeamsForBusiness

        async with translate_graph_errors():
            created = await self.graph.me.events.post(event)
        if created is None:
            raise GraphError("Graph returned no body when creating the event")
        return _event(created)

    async def update_event(
        self,
        event_id: str,
        *,
        subject: str | None = None,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        time_zone: str = "UTC",
        body: str | None = None,
        body_type: str = "text",
        location: str | None = None,
    ) -> Event:
        """Change the given fields of an event; attendees are notified of the update.

        Raises:
            ValueError: Nothing to change, or a time argument is malformed.
        """
        _check_id(event_id, "event_id")
        if all(v is None for v in (subject, start, end, body, location)):
            raise ValueError("nothing to update: pass at least one field")
        patch = GraphEvent()
        if subject is not None:
            patch.subject = subject
        if start is not None:
            patch.start = DateTimeTimeZone(
                date_time=_iso_local(start, time_zone), time_zone=time_zone
            )
        if end is not None:
            patch.end = DateTimeTimeZone(date_time=_iso_local(end, time_zone), time_zone=time_zone)
        if body is not None:
            patch.body = ItemBody(content_type=_check_body_type(body_type), content=body)
        if location is not None:
            patch.location = Location(display_name=location)

        async with translate_graph_errors():
            updated = await self.graph.me.events.by_event_id(event_id).patch(patch)
        if updated is None:
            raise GraphError("Graph returned no body for the updated event")
        return _event(updated)

    async def respond_event(
        self,
        event_id: str,
        response: str,
        *,
        comment: str | None = None,
        send_response: bool = True,
    ) -> None:
        """Accept, decline, or tentatively accept an invitation.

        Args:
            response: ``"accept"``, ``"decline"`` or ``"tentative"``.
            comment: Optional note to the organizer.
            send_response: Whether the organizer is notified.
        """
        _check_id(event_id, "event_id")
        if response not in _EVENT_RESPONSES:
            raise ValueError(f"response must be one of {sorted(_EVENT_RESPONSES)}, got {response!r}")
        builder = self.graph.me.events.by_event_id(event_id)
        async with translate_graph_errors():
            if response == "accept":
                await builder.accept.post(
                    AcceptPostRequestBody(comment=comment, send_response=send_response)
                )
            elif response == "decline":
                await builder.decline.post(
                    DeclinePostRequestBody(comment=comment, send_response=send_response)
                )
            else:
                await builder.tentatively_accept.post(
                    TentativelyAcceptPostRequestBody(comment=comment, send_response=send_response)
                )

    async def delete_event(self, event_id: str) -> None:
        """Delete an event. If the user organised it, attendees receive a cancellation."""
        _check_id(event_id, "event_id")
        async with translate_graph_errors():
            await self.graph.me.events.by_event_id(event_id).delete()

    # ---- contacts -----------------------------------------------------------

    async def list_contacts(
        self, *, top: int = DEFAULT_TOP, name_starts_with: str | None = None
    ) -> list[Contact]:
        """The user's personal contacts, by display name.

        Args:
            top: Maximum number of contacts, 1 to :data:`MAX_TOP`.
            name_starts_with: Prefix match on the display name (Graph's
                contacts collection supports ``$filter``, not ``$search``).
        """
        check_top(top)
        query = ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters(
            top=top,
            select=_CONTACT_SELECT,
            orderby=["displayName"],
            filter=(
                f"startswith(displayName,'{name_starts_with.strip().replace(chr(39), chr(39) * 2)}')"
                if name_starts_with and name_starts_with.strip()
                else None
            ),
        )
        config = RequestConfiguration(query_parameters=query)
        async with translate_graph_errors():
            items = await collect(
                self.graph.me.contacts,
                lambda b: b.get(request_configuration=config),
                max_items=top,
            )
        return [_contact(c) for c in items]
