"""Outlook tools: mail (read, search, send, reply, forward, drafts, move),
attachments, calendar, and contacts.

Thin adapters over :class:`outlook_client.OutlookClient`. All mailbox
knowledge (which Graph calls, ``$select`` lists, paging, the
filter-plus-orderby rule) stays in the SDK; these classes own only what an
LLM tool needs — a name, routing guidance, a small validated schema, and a
bounded JSON result.

Every argument is re-validated inside ``_arun()``: some hosts (Apex among
them) call ``_arun`` directly rather than going through ``ainvoke()``, so
Pydantic constraints on the schema alone are not guaranteed to have run.

Write tools say so in their descriptions and are not idempotent — the model
is told not to repeat a call that already succeeded — and every write goes
out as the signed-in user: sent mail leaves their mailbox, events land on
their calendar.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from langchain_core.tools import BaseTool
from outlook_client import DEFAULT_TOP, MAX_TOP, OutlookClient
from pydantic import BaseModel, Field

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
    "CreateOutlookDraftTool",
    "CreateOutlookEventTool",
    "ForwardOutlookMessageTool",
    "GetOutlookMessageTool",
    "ListOutlookContactsTool",
    "ListOutlookEventsTool",
    "ListOutlookFoldersTool",
    "ListOutlookMessagesTool",
    "MoveOutlookMessageTool",
    "ReadOutlookAttachmentTool",
    "ReplyOutlookMessageTool",
    "RespondOutlookEventTool",
    "SendOutlookMessageTool",
    "outlook_tools",
]

_TOP_DESCRIPTION = f"How many items to return, 1-{MAX_TOP}."
_MAX_CHARS_DESCRIPTION = "Cap on the returned text length; longer content is truncated."
_ADDRESSES_DESCRIPTION = "Email addresses, e.g. ['ada@contoso.com']."
_BODY_TYPE_DESCRIPTION = "'text' for plain text (default) or 'html'."


def _addresses(description: str, *, required: bool) -> Any:
    if required:
        return Field(min_length=1, max_length=50, description=description)
    return Field(default_factory=list, max_length=50, description=description)


# ═════════════════════════════════════════════════════════════════════
# Mail — read
# ═════════════════════════════════════════════════════════════════════


class ListOutlookMessagesInput(BaseModel):
    top: int = Field(default=DEFAULT_TOP, ge=1, le=MAX_TOP, description=_TOP_DESCRIPTION)
    folder: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Restrict to one folder: a well-known name ('inbox', 'sentitems', 'drafts', "
            "'archive', 'deleteditems') or a folder ID from list_outlook_folders. "
            "Omit to list across the whole mailbox."
        ),
    )
    unread_only: bool = Field(default=False, description="Return only unread messages.")
    search: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Full-text search over subject, body and sender, e.g. 'invoice March'. "
            "Results come back by relevance instead of date, and search cannot be "
            "combined with unread_only."
        ),
    )


class ListOutlookMessagesTool(M365BaseTool):
    name: str = "list_outlook_messages"
    description: str = (
        "List or search the signed-in user's Outlook email messages, newest first (or by "
        "relevance when 'search' is given). Returns message IDs, subjects, senders, dates, "
        "read state, an attachment flag and a one-line preview — never full bodies; pass an "
        "'id' from this result to get_outlook_message to read one. Only this user's own "
        "mailbox is accessible. Not for SharePoint files, calendars, or web information. "
        "Read-only, cheap, safe to repeat."
    )
    args_schema: type[BaseModel] = ListOutlookMessagesInput

    async def _arun(
        self,
        top: int = DEFAULT_TOP,
        folder: str | None = None,
        unread_only: bool = False,
        search: str | None = None,
        **_: Any,
    ) -> str:
        args = ListOutlookMessagesInput(top=top, folder=folder, unread_only=unread_only, search=search)
        graph = await self._graph()
        try:
            messages = await OutlookClient(graph).list_messages(
                top=args.top, folder=args.folder, unread_only=args.unread_only, search=args.search
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not messages:
            scope = f"folder '{args.folder}'" if args.folder else "the mailbox"
            extra = " that are unread" if args.unread_only else ""
            found = f" matching '{args.search}'" if args.search else ""
            return f"No messages{extra}{found} found in {scope}."
        return dump_json({"count": len(messages), "messages": [asdict(m) for m in messages]})


class GetOutlookMessageInput(BaseModel):
    message_id: str = Field(
        min_length=1,
        max_length=512,
        description="A message ID exactly as returned by list_outlook_messages.",
    )
    max_chars: int = Field(default=20_000, ge=200, le=MAX_TEXT_CHARS, description=_MAX_CHARS_DESCRIPTION)


class GetOutlookMessageTool(M365BaseTool):
    name: str = "get_outlook_message"
    description: str = (
        "Read one Outlook email in full — body, sender, recipients, and the list of its "
        "attachments (name, type, size, ID) — by its ID. IDs are opaque and cannot be guessed "
        "or reconstructed: always copy one exactly from a list_outlook_messages result first. "
        "The body may be HTML. To read a text attachment, pass its ID to "
        "read_outlook_attachment. Read-only."
    )
    args_schema: type[BaseModel] = GetOutlookMessageInput

    async def _arun(self, message_id: str, max_chars: int = 20_000, **_: Any) -> str:
        args = GetOutlookMessageInput(message_id=message_id, max_chars=max_chars)
        graph = await self._graph()
        outlook = OutlookClient(graph)
        try:
            message = await outlook.get_message(args.message_id)
            attachments = (
                await outlook.list_attachments(args.message_id) if message.has_attachments else []
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        truncated = bool(message.body_content and len(message.body_content) > args.max_chars)
        if truncated:
            message = replace(message, body_content=message.body_content[: args.max_chars])
        payload = asdict(message)
        payload["body_truncated"] = truncated
        payload["attachments"] = [asdict(a) for a in attachments]
        return dump_json(payload)


class ListOutlookFoldersInput(BaseModel):
    top: int = Field(default=20, ge=1, le=MAX_TOP, description=_TOP_DESCRIPTION)


class ListOutlookFoldersTool(M365BaseTool):
    name: str = "list_outlook_folders"
    description: str = (
        "List the signed-in user's top-level Outlook mail folders with their message and "
        "unread counts. Use a folder's 'id' (or a well-known name like 'inbox') to scope "
        "list_outlook_messages or as a move_outlook_message destination. Only needed for "
        "folders beyond the well-known ones. Read-only."
    )
    args_schema: type[BaseModel] = ListOutlookFoldersInput

    async def _arun(self, top: int = 20, **_: Any) -> str:
        args = ListOutlookFoldersInput(top=top)
        graph = await self._graph()
        try:
            folders = await OutlookClient(graph).list_folders(top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not folders:
            return "The mailbox has no folders."
        return dump_json({"count": len(folders), "folders": [asdict(f) for f in folders]})


class ReadOutlookAttachmentInput(BaseModel):
    message_id: str = Field(min_length=1, max_length=512, description="The message's ID.")
    attachment_id: str = Field(
        min_length=1,
        max_length=512,
        description="An attachment ID exactly as listed by get_outlook_message.",
    )
    max_chars: int = Field(default=20_000, ge=200, le=MAX_TEXT_CHARS, description=_MAX_CHARS_DESCRIPTION)


class ReadOutlookAttachmentTool(M365BaseTool):
    name: str = "read_outlook_attachment"
    description: str = (
        "Read the text content of an email attachment — plain text, Markdown, CSV, JSON, "
        "XML, HTML, source code. Requires the message ID and the attachment ID from "
        "get_outlook_message. Binary formats (docx, xlsx, pdf, images) cannot be read by "
        "this tool and are refused with the file's type. Read-only."
    )
    args_schema: type[BaseModel] = ReadOutlookAttachmentInput

    async def _arun(
        self, message_id: str, attachment_id: str, max_chars: int = 20_000, **_: Any
    ) -> str:
        args = ReadOutlookAttachmentInput(
            message_id=message_id, attachment_id=attachment_id, max_chars=max_chars
        )
        graph = await self._graph()
        try:
            downloaded = await OutlookClient(graph).download_attachment(
                args.message_id, args.attachment_id
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        meta = downloaded.attachment
        if not is_text_mime(meta.content_type):
            return (
                f"'{meta.name}' has type '{meta.content_type or 'unknown'}', which this tool "
                "cannot render as text. Only text-based formats are readable; report the "
                "attachment's name and type to the user instead of retrying."
            )
        text = downloaded.content.decode("utf-8", errors="replace")
        return dump_json(
            {
                "name": meta.name,
                "content_type": meta.content_type,
                "size_bytes": meta.size,
                "truncated": len(text) > args.max_chars,
                "text": text[: args.max_chars],
            }
        )


# ═════════════════════════════════════════════════════════════════════
# Mail — write
# ═════════════════════════════════════════════════════════════════════


class SendOutlookMessageInput(BaseModel):
    to: list[str] = _addresses(_ADDRESSES_DESCRIPTION, required=True)
    subject: str = Field(min_length=1, max_length=255, description="The subject line.")
    body: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, description="The message body.")
    cc: list[str] = _addresses("CC addresses.", required=False)
    bcc: list[str] = _addresses("BCC addresses.", required=False)
    body_type: str = Field(default="text", pattern="^(text|html)$", description=_BODY_TYPE_DESCRIPTION)


class SendOutlookMessageTool(M365BaseTool):
    name: str = "send_outlook_message"
    description: str = (
        "Send a new email immediately from the signed-in user's mailbox. SIDE EFFECT: the "
        "message is delivered as soon as this returns and cannot be recalled; do not call it "
        "twice for the same email, and prefer create_outlook_draft when the user should "
        "review before sending. Use reply_outlook_message or forward_outlook_message for "
        "an existing conversation."
    )
    args_schema: type[BaseModel] = SendOutlookMessageInput

    async def _arun(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_type: str = "text",
        **_: Any,
    ) -> str:
        args = SendOutlookMessageInput(
            to=to, subject=subject, body=body, cc=cc or [], bcc=bcc or [], body_type=body_type
        )
        graph = await self._graph()
        try:
            await OutlookClient(graph).send_message(
                subject=args.subject,
                body=args.body,
                to=args.to,
                cc=args.cc,
                bcc=args.bcc,
                body_type=args.body_type,
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"sent": True, "to": args.to, "cc": args.cc, "subject": args.subject})


class CreateOutlookDraftInput(BaseModel):
    subject: str = Field(min_length=1, max_length=255, description="The subject line.")
    body: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, description="The message body.")
    to: list[str] = _addresses(_ADDRESSES_DESCRIPTION + " May be empty for a draft.", required=False)
    cc: list[str] = _addresses("CC addresses.", required=False)
    bcc: list[str] = _addresses("BCC addresses.", required=False)
    body_type: str = Field(default="text", pattern="^(text|html)$", description=_BODY_TYPE_DESCRIPTION)


class CreateOutlookDraftTool(M365BaseTool):
    name: str = "create_outlook_draft"
    description: str = (
        "Save a new email as a draft in the signed-in user's Drafts folder WITHOUT sending "
        "it, so the user can review and send it themselves. Returns the draft's ID and a "
        "web link that opens it in Outlook. The safe choice whenever the user has not "
        "explicitly asked to send. Creates one draft per call."
    )
    args_schema: type[BaseModel] = CreateOutlookDraftInput

    async def _arun(
        self,
        subject: str,
        body: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_type: str = "text",
        **_: Any,
    ) -> str:
        args = CreateOutlookDraftInput(
            subject=subject, body=body, to=to or [], cc=cc or [], bcc=bcc or [], body_type=body_type
        )
        graph = await self._graph()
        try:
            draft = await OutlookClient(graph).create_draft(
                subject=args.subject,
                body=args.body,
                to=args.to,
                cc=args.cc,
                bcc=args.bcc,
                body_type=args.body_type,
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json(
            {"draft_id": draft.id, "subject": draft.subject, "web_link": draft.web_link}
        )


class ReplyOutlookMessageInput(BaseModel):
    message_id: str = Field(min_length=1, max_length=512, description="The message to reply to.")
    comment: str = Field(
        min_length=1,
        max_length=MAX_TEXT_CHARS,
        description="The reply text; Outlook quotes the original below it.",
    )
    reply_all: bool = Field(
        default=False,
        description="Reply to every original recipient instead of only the sender.",
    )


class ReplyOutlookMessageTool(M365BaseTool):
    name: str = "reply_outlook_message"
    description: str = (
        "Reply to an existing email, keeping the thread. SIDE EFFECT: sends immediately as "
        "the signed-in user; not reversible, do not call twice for the same reply. Use "
        "reply_all=true only when the user asks to answer everyone. Requires a message ID "
        "from list_outlook_messages."
    )
    args_schema: type[BaseModel] = ReplyOutlookMessageInput

    async def _arun(self, message_id: str, comment: str, reply_all: bool = False, **_: Any) -> str:
        args = ReplyOutlookMessageInput(message_id=message_id, comment=comment, reply_all=reply_all)
        graph = await self._graph()
        try:
            await OutlookClient(graph).reply_message(
                args.message_id, args.comment, reply_all=args.reply_all
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"sent": True, "message_id": args.message_id, "reply_all": args.reply_all})


class ForwardOutlookMessageInput(BaseModel):
    message_id: str = Field(min_length=1, max_length=512, description="The message to forward.")
    to: list[str] = _addresses(_ADDRESSES_DESCRIPTION, required=True)
    comment: str = Field(default="", max_length=MAX_TEXT_CHARS, description="Optional note above the forwarded message.")


class ForwardOutlookMessageTool(M365BaseTool):
    name: str = "forward_outlook_message"
    description: str = (
        "Forward an existing email, with its attachments, to other people. SIDE EFFECT: "
        "sends immediately as the signed-in user; not reversible, do not call twice. "
        "Requires a message ID from list_outlook_messages."
    )
    args_schema: type[BaseModel] = ForwardOutlookMessageInput

    async def _arun(self, message_id: str, to: list[str], comment: str = "", **_: Any) -> str:
        args = ForwardOutlookMessageInput(message_id=message_id, to=to, comment=comment)
        graph = await self._graph()
        try:
            await OutlookClient(graph).forward_message(args.message_id, to=args.to, comment=args.comment)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"sent": True, "message_id": args.message_id, "to": args.to})


class MoveOutlookMessageInput(BaseModel):
    message_id: str = Field(min_length=1, max_length=512, description="The message to move.")
    destination_folder: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "A well-known folder name ('archive', 'deleteditems', 'inbox', 'junkemail') or a "
            "folder ID from list_outlook_folders."
        ),
    )


class MoveOutlookMessageTool(M365BaseTool):
    name: str = "move_outlook_message"
    description: str = (
        "Move an email to another folder — archive it ('archive') or delete it reversibly "
        "('deleteditems'). SIDE EFFECT: the message gets a NEW ID after the move; use the "
        "returned id for any later call. Nothing is sent. Permanent deletion is "
        "deliberately not available to this tool."
    )
    args_schema: type[BaseModel] = MoveOutlookMessageInput

    async def _arun(self, message_id: str, destination_folder: str, **_: Any) -> str:
        args = MoveOutlookMessageInput(message_id=message_id, destination_folder=destination_folder)
        graph = await self._graph()
        try:
            moved = await OutlookClient(graph).move_message(args.message_id, args.destination_folder)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json(
            {"moved": True, "destination_folder": args.destination_folder, "new_id": moved.id,
             "subject": moved.subject}
        )


# ═════════════════════════════════════════════════════════════════════
# Calendar
# ═════════════════════════════════════════════════════════════════════

_TIME_DESCRIPTION = "ISO 8601 date-time without offset, e.g. '2026-09-03T14:00:00'."
_TIME_ZONE_DESCRIPTION = (
    "Time zone the times are expressed in: 'UTC' (default), an IANA name such as "
    "'Europe/London', or a Windows name such as 'Pacific Standard Time'."
)


class ListOutlookEventsInput(BaseModel):
    start: str | None = Field(default=None, max_length=64, description="Window start. " + _TIME_DESCRIPTION)
    end: str | None = Field(default=None, max_length=64, description="Window end. " + _TIME_DESCRIPTION)
    top: int = Field(default=DEFAULT_TOP, ge=1, le=MAX_TOP, description=_TOP_DESCRIPTION)


class ListOutlookEventsTool(M365BaseTool):
    name: str = "list_outlook_events"
    description: str = (
        "List the signed-in user's calendar events. Give both 'start' and 'end' (UTC) to "
        "get everything scheduled in that window, recurring meetings expanded — the right "
        "way to answer 'what is on my calendar today/this week'. Omit both to list the "
        "next upcoming event objects. Returns times with their time zone, location, "
        "organizer, attendees and their responses, and online-meeting links. Read-only."
    )
    args_schema: type[BaseModel] = ListOutlookEventsInput

    async def _arun(
        self, start: str | None = None, end: str | None = None, top: int = DEFAULT_TOP, **_: Any
    ) -> str:
        args = ListOutlookEventsInput(start=start, end=end, top=top)
        graph = await self._graph()
        try:
            events = await OutlookClient(graph).list_events(start=args.start, end=args.end, top=args.top)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not events:
            window = f" between {args.start} and {args.end}" if args.start else ""
            return f"No calendar events{window}."
        return dump_json({"count": len(events), "events": [asdict(e) for e in events]})


class CreateOutlookEventInput(BaseModel):
    subject: str = Field(min_length=1, max_length=255, description="The event title.")
    start: str = Field(min_length=1, max_length=64, description=_TIME_DESCRIPTION)
    end: str = Field(min_length=1, max_length=64, description=_TIME_DESCRIPTION)
    time_zone: str = Field(default="UTC", min_length=1, max_length=64, description=_TIME_ZONE_DESCRIPTION)
    attendees: list[str] = _addresses("Attendee email addresses; each receives an invitation.", required=False)
    location: str | None = Field(default=None, max_length=255, description="Room or place.")
    body: str | None = Field(default=None, max_length=MAX_TEXT_CHARS, description="Agenda or notes.")
    online_meeting: bool = Field(default=False, description="Also create a Teams meeting link.")


class CreateOutlookEventTool(M365BaseTool):
    name: str = "create_outlook_event"
    description: str = (
        "Create an event on the signed-in user's calendar. SIDE EFFECT: every attendee is "
        "sent an invitation immediately, so confirm the details with the user first and do "
        "not call twice for the same meeting. Returns the event with its ID, web link and, "
        "when requested, a Teams join link. Times are wall-clock in 'time_zone'."
    )
    args_schema: type[BaseModel] = CreateOutlookEventInput

    async def _arun(
        self,
        subject: str,
        start: str,
        end: str,
        time_zone: str = "UTC",
        attendees: list[str] | None = None,
        location: str | None = None,
        body: str | None = None,
        online_meeting: bool = False,
        **_: Any,
    ) -> str:
        args = CreateOutlookEventInput(
            subject=subject,
            start=start,
            end=end,
            time_zone=time_zone,
            attendees=attendees or [],
            location=location,
            body=body,
            online_meeting=online_meeting,
        )
        graph = await self._graph()
        try:
            event = await OutlookClient(graph).create_event(
                subject=args.subject,
                start=args.start,
                end=args.end,
                time_zone=args.time_zone,
                attendees=args.attendees,
                location=args.location,
                body=args.body,
                online_meeting=args.online_meeting,
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"created": True, **asdict(event)})


class RespondOutlookEventInput(BaseModel):
    event_id: str = Field(min_length=1, max_length=512, description="An event ID from list_outlook_events.")
    response: str = Field(
        pattern="^(accept|decline|tentative)$",
        description="'accept', 'decline', or 'tentative'.",
    )
    comment: str | None = Field(default=None, max_length=2000, description="Optional note to the organizer.")


class RespondOutlookEventTool(M365BaseTool):
    name: str = "respond_outlook_event"
    description: str = (
        "Accept, decline, or tentatively accept a meeting invitation on the signed-in "
        "user's calendar. SIDE EFFECT: the organizer is notified of the response. Requires "
        "an event ID from list_outlook_events. Only meaningful for events the user was "
        "invited to, not ones they organise."
    )
    args_schema: type[BaseModel] = RespondOutlookEventInput

    async def _arun(self, event_id: str, response: str, comment: str | None = None, **_: Any) -> str:
        args = RespondOutlookEventInput(event_id=event_id, response=response, comment=comment)
        graph = await self._graph()
        try:
            await OutlookClient(graph).respond_event(args.event_id, args.response, comment=args.comment)
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)
        return dump_json({"responded": True, "event_id": args.event_id, "response": args.response})


# ═════════════════════════════════════════════════════════════════════
# Contacts
# ═════════════════════════════════════════════════════════════════════


class ListOutlookContactsInput(BaseModel):
    name_starts_with: str | None = Field(
        default=None,
        max_length=128,
        description="Prefix of the contact's display name, e.g. 'Ada'. Omit to list all.",
    )
    top: int = Field(default=DEFAULT_TOP, ge=1, le=MAX_TOP, description=_TOP_DESCRIPTION)


class ListOutlookContactsTool(M365BaseTool):
    name: str = "list_outlook_contacts"
    description: str = (
        "Look up the signed-in user's personal Outlook contacts — names, email addresses, "
        "company, job title, phone numbers. Use it to find an email address before "
        "sending, replying or inviting. Prefix-matches on display name. Read-only."
    )
    args_schema: type[BaseModel] = ListOutlookContactsInput

    async def _arun(self, name_starts_with: str | None = None, top: int = DEFAULT_TOP, **_: Any) -> str:
        args = ListOutlookContactsInput(name_starts_with=name_starts_with, top=top)
        graph = await self._graph()
        try:
            contacts = await OutlookClient(graph).list_contacts(
                top=args.top, name_starts_with=args.name_starts_with
            )
        except RECOVERABLE_ERRORS as exc:
            return recoverable_error_text(exc)

        if not contacts:
            hint = f" starting with '{args.name_starts_with}'" if args.name_starts_with else ""
            return f"No contacts{hint} in the user's address book."
        return dump_json({"count": len(contacts), "contacts": [asdict(c) for c in contacts]})


# ═════════════════════════════════════════════════════════════════════
# Factory
# ═════════════════════════════════════════════════════════════════════

_READ_TOOLS: tuple[type[M365BaseTool], ...] = (
    ListOutlookMessagesTool,
    GetOutlookMessageTool,
    ListOutlookFoldersTool,
    ReadOutlookAttachmentTool,
    ListOutlookEventsTool,
    ListOutlookContactsTool,
)
_WRITE_TOOLS: tuple[type[M365BaseTool], ...] = (
    SendOutlookMessageTool,
    CreateOutlookDraftTool,
    ReplyOutlookMessageTool,
    ForwardOutlookMessageTool,
    MoveOutlookMessageTool,
    CreateOutlookEventTool,
    RespondOutlookEventTool,
)


def outlook_tools(graph_provider: GraphProvider, *, include_writes: bool = True) -> list[BaseTool]:
    """Fresh Outlook tool instances bound to ``graph_provider``.

    Call once per agent execution and hand the result to ``bind_tools``.
    Instances hold execution-specific state (the provider), so never reuse
    them across concurrent executions. ``include_writes=False`` gives a
    read-only agent the six read tools and nothing that sends, moves, or
    creates.
    """
    classes = _READ_TOOLS + (_WRITE_TOOLS if include_writes else ())
    return [cls(graph_provider=graph_provider) for cls in classes]
