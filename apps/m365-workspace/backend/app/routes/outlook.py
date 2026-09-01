"""Outlook routes — the HTTP face of ``outlook-client``.

Every handler is one ``OutlookClient(graph).<method>(...)`` call plus
request/response shaping. The SDK owns the Graph calls and the models;
``app/graph.py`` owns identity (the caller's own Graph client) and the
mapping of the SDK's typed errors; ``app/main.py`` maps the SDK's argument
checks (``ValueError``) to 400s. Nothing here catches an SDK error.

Writes return the smallest useful thing: a ``{"status": "sent"}`` 202 for
operations Graph performs asynchronously (send, reply, forward), the new
resource for creates (drafts, events, attachments), and 204 for deletes and
state changes. ``move`` returns the message under its **new** id, because
Graph re-ids on move and the old id stops working.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from msgraph import GraphServiceClient
from outlook_client import (
    DEFAULT_TOP,
    MAX_TOP,
    Attachment,
    Contact,
    Event,
    MailFolder,
    MessageDetail,
    MessageSummary,
    OutlookClient,
    UserProfile,
)
from pydantic import BaseModel, Field

from app.graph import get_graph

router = APIRouter(prefix="/v1/outlook", tags=["outlook"])

Graph = Annotated[GraphServiceClient, Depends(get_graph)]
Top = Annotated[int, Query(ge=1, le=MAX_TOP)]
BodyType = Literal["text", "html"]


def content_disposition(name: str) -> str:
    """An ``attachment`` disposition that survives non-ASCII file names."""
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# ── Request / response models ────────────────────────────────────────


class SentResponse(BaseModel):
    status: Literal["sent"] = "sent"


class SendMessageRequest(BaseModel):
    to: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=255)
    body: str = ""
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    body_type: BodyType = "text"
    save_to_sent: bool = True


class DraftRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    body_type: BodyType = "text"


class ReplyRequest(BaseModel):
    comment: str = ""
    reply_all: bool = False


class ForwardRequest(BaseModel):
    to: list[str] = Field(min_length=1)
    comment: str = ""


class MoveRequest(BaseModel):
    destination_folder: str = Field(min_length=1)


class ReadStateRequest(BaseModel):
    read: bool = True


class EventCreateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    time_zone: str = "UTC"
    body: str | None = None
    body_type: BodyType = "text"
    attendees: list[str] = Field(default_factory=list)
    location: str | None = None
    is_all_day: bool = False
    online_meeting: bool = False


class EventUpdateRequest(BaseModel):
    subject: str | None = None
    start: str | None = None
    end: str | None = None
    time_zone: str = "UTC"
    body: str | None = None
    body_type: BodyType = "text"
    location: str | None = None


class RespondRequest(BaseModel):
    response: Literal["accept", "decline", "tentative"]
    comment: str | None = None
    send_response: bool = True


# ── Profile and folders ──────────────────────────────────────────────


@router.get("/profile", response_model=UserProfile)
async def profile(graph: Graph) -> UserProfile:
    return await OutlookClient(graph).get_profile()


@router.get("/folders", response_model=list[MailFolder])
async def folders(graph: Graph, top: Top = 20) -> list[MailFolder]:
    return await OutlookClient(graph).list_folders(top=top)


# ── Messages ─────────────────────────────────────────────────────────


@router.get("/messages", response_model=list[MessageSummary])
async def messages(
    graph: Graph,
    top: Top = DEFAULT_TOP,
    folder: Annotated[str | None, Query(max_length=256)] = None,
    unread_only: bool = False,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[MessageSummary]:
    return await OutlookClient(graph).list_messages(
        top=top, folder=folder, unread_only=unread_only, search=search
    )


@router.post("/messages/send", status_code=202, response_model=SentResponse)
async def send_message(graph: Graph, body: SendMessageRequest) -> SentResponse:
    await OutlookClient(graph).send_message(
        subject=body.subject,
        body=body.body,
        to=body.to,
        cc=body.cc,
        bcc=body.bcc,
        body_type=body.body_type,
        save_to_sent=body.save_to_sent,
    )
    return SentResponse()


@router.post("/messages/drafts", status_code=201, response_model=MessageDetail)
async def create_draft(graph: Graph, body: DraftRequest) -> MessageDetail:
    return await OutlookClient(graph).create_draft(
        subject=body.subject,
        body=body.body,
        to=body.to,
        cc=body.cc,
        bcc=body.bcc,
        body_type=body.body_type,
    )


@router.get("/messages/{message_id}", response_model=MessageDetail)
async def get_message(graph: Graph, message_id: str) -> MessageDetail:
    return await OutlookClient(graph).get_message(message_id)


@router.delete("/messages/{message_id}", status_code=204, response_class=Response)
async def delete_message(graph: Graph, message_id: str, permanent: bool = False) -> Response:
    await OutlookClient(graph).delete_message(message_id, permanent=permanent)
    return Response(status_code=204)


@router.patch("/messages/{message_id}/read", status_code=204, response_class=Response)
async def set_read(graph: Graph, message_id: str, body: ReadStateRequest) -> Response:
    await OutlookClient(graph).set_read(message_id, read=body.read)
    return Response(status_code=204)


@router.post("/messages/{message_id}/send", status_code=202, response_model=SentResponse)
async def send_draft(graph: Graph, message_id: str) -> SentResponse:
    await OutlookClient(graph).send_draft(message_id)
    return SentResponse()


@router.post("/messages/{message_id}/reply", status_code=202, response_model=SentResponse)
async def reply(graph: Graph, message_id: str, body: ReplyRequest) -> SentResponse:
    await OutlookClient(graph).reply_message(message_id, body.comment, reply_all=body.reply_all)
    return SentResponse()


@router.post("/messages/{message_id}/forward", status_code=202, response_model=SentResponse)
async def forward(graph: Graph, message_id: str, body: ForwardRequest) -> SentResponse:
    await OutlookClient(graph).forward_message(message_id, to=body.to, comment=body.comment)
    return SentResponse()


@router.post("/messages/{message_id}/move", response_model=MessageDetail)
async def move(graph: Graph, message_id: str, body: MoveRequest) -> MessageDetail:
    return await OutlookClient(graph).move_message(message_id, body.destination_folder)


# ── Attachments ──────────────────────────────────────────────────────


@router.get("/messages/{message_id}/attachments", response_model=list[Attachment])
async def attachments(graph: Graph, message_id: str, top: Top = MAX_TOP) -> list[Attachment]:
    return await OutlookClient(graph).list_attachments(message_id, top=top)


@router.post("/messages/{message_id}/attachments", status_code=201, response_model=Attachment)
async def add_attachment(
    graph: Graph, message_id: str, file: Annotated[UploadFile, File()]
) -> Attachment:
    """Attach an uploaded file to a draft (Graph's 3 MB single-request limit applies)."""
    data = await file.read()
    return await OutlookClient(graph).add_attachment(
        message_id,
        name=file.filename or "attachment",
        content=data,
        content_type=file.content_type or "application/octet-stream",
    )


@router.get(
    "/messages/{message_id}/attachments/{attachment_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def attachment_content(graph: Graph, message_id: str, attachment_id: str) -> Response:
    downloaded = await OutlookClient(graph).download_attachment(message_id, attachment_id)
    meta = downloaded.attachment
    return Response(
        content=downloaded.content,
        media_type=meta.content_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(meta.name or "attachment")},
    )


# ── Calendar ─────────────────────────────────────────────────────────


@router.get("/events", response_model=list[Event])
async def events(
    graph: Graph,
    start: str | None = None,
    end: str | None = None,
    top: Top = DEFAULT_TOP,
) -> list[Event]:
    """Calendar view when both bounds are given (recurrences expanded); upcoming events otherwise."""
    return await OutlookClient(graph).list_events(start=start, end=end, top=top)


@router.post("/events", status_code=201, response_model=Event)
async def create_event(graph: Graph, body: EventCreateRequest) -> Event:
    return await OutlookClient(graph).create_event(
        subject=body.subject,
        start=body.start,
        end=body.end,
        time_zone=body.time_zone,
        body=body.body,
        body_type=body.body_type,
        attendees=body.attendees,
        location=body.location,
        is_all_day=body.is_all_day,
        online_meeting=body.online_meeting,
    )


@router.get("/events/{event_id}", response_model=Event)
async def get_event(graph: Graph, event_id: str) -> Event:
    return await OutlookClient(graph).get_event(event_id)


@router.patch("/events/{event_id}", response_model=Event)
async def update_event(graph: Graph, event_id: str, body: EventUpdateRequest) -> Event:
    return await OutlookClient(graph).update_event(
        event_id,
        subject=body.subject,
        start=body.start,
        end=body.end,
        time_zone=body.time_zone,
        body=body.body,
        body_type=body.body_type,
        location=body.location,
    )


@router.post("/events/{event_id}/respond", status_code=204, response_class=Response)
async def respond_event(graph: Graph, event_id: str, body: RespondRequest) -> Response:
    await OutlookClient(graph).respond_event(
        event_id, body.response, comment=body.comment, send_response=body.send_response
    )
    return Response(status_code=204)


@router.delete("/events/{event_id}", status_code=204, response_class=Response)
async def delete_event(graph: Graph, event_id: str) -> Response:
    await OutlookClient(graph).delete_event(event_id)
    return Response(status_code=204)


# ── Contacts ─────────────────────────────────────────────────────────


@router.get("/contacts", response_model=list[Contact])
async def contacts(
    graph: Graph,
    top: Top = DEFAULT_TOP,
    name_starts_with: Annotated[str | None, Query(max_length=100)] = None,
) -> list[Contact]:
    return await OutlookClient(graph).list_contacts(top=top, name_starts_with=name_starts_with)
