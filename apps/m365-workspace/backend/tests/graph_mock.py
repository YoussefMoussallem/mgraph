"""A mock Microsoft Graph behind a real ``M365Client``.

``make_m365()`` returns an ``M365Client`` whose credential provider hands
out a static token and whose HTTP transport is ``httpx.MockTransport`` over
``handler`` below — so the SDKs build real requests, page real responses
and translate real error bodies, and the tests only fake the tenant.

Every request is appended to ``LOG`` so a test can assert what went over
the wire (``last("POST", "/me/sendMail")``), not just what came back.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from azure.core.credentials import AccessToken
from m365_client import M365Client, M365Settings

LOG: list[dict[str, Any]] = []


def _ea(name: str, address: str) -> dict:
    return {"emailAddress": {"name": name, "address": address}}


MSG = {
    "id": "AAMk-1",
    "subject": "Hello",
    "isRead": False,
    "hasAttachments": True,
    "webLink": "https://outlook/AAMk-1",
    "from": _ea("Ada", "ada@x.com"),
    "receivedDateTime": "2026-08-20T09:14:00Z",
    "bodyPreview": "hi",
    "body": {"contentType": "text", "content": "Body text"},
    "toRecipients": [_ea("Me", "me@x.com")],
    "ccRecipients": [_ea("Cc", "cc@x.com")],
}
ATT_TXT = {
    "@odata.type": "#microsoft.graph.fileAttachment",
    "id": "att-1",
    "name": "notes.txt",
    "contentType": "text/plain",
    "size": 11,
    "isInline": False,
    "contentBytes": base64.b64encode(b"hello notes").decode(),
}
ATT_PDF = {
    "@odata.type": "#microsoft.graph.fileAttachment",
    "id": "att-2",
    "name": "deck.pdf",
    "contentType": "application/pdf",
    "size": 9,
    "isInline": False,
    "contentBytes": base64.b64encode(b"%PDF-1.4x").decode(),
}
FOLDERS = [{"id": "f-1", "displayName": "Inbox", "totalItemCount": 3, "unreadItemCount": 1}]
EVENT = {
    "id": "ev-1",
    "subject": "Standup",
    "isAllDay": False,
    "isCancelled": False,
    "start": {"dateTime": "2026-09-03T09:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-09-03T09:30:00.0000000", "timeZone": "UTC"},
    "location": {"displayName": "Room 1"},
    "organizer": _ea("Ada", "ada@x.com"),
    "attendees": [
        {
            **_ea("Me", "me@x.com"),
            "type": "required",
            "status": {"response": "accepted", "time": "2026-09-01T00:00:00Z"},
        }
    ],
    "isOnlineMeeting": True,
    "onlineMeeting": {"joinUrl": "https://teams/join"},
    "webLink": "https://outlook/ev-1",
    "bodyPreview": "daily",
    "responseStatus": {"response": "organizer"},
}
CONTACT = {
    "id": "c-1",
    "displayName": "Ada Lovelace",
    "givenName": "Ada",
    "surname": "Lovelace",
    "emailAddresses": [{"name": "Ada", "address": "ada@x.com"}],
    "companyName": "Analytical",
    "jobTitle": "Engineer",
    "mobilePhone": "+1 555",
    "businessPhones": ["+1 444"],
}
PROFILE = {
    "id": "oid-1234",
    "displayName": "Test User",
    "mail": "user@example.com",
    "userPrincipalName": "user@example.com",
    "jobTitle": "Analyst",
    "officeLocation": "Dubai",
}
SITE = {
    "id": "contoso.sharepoint.com,s1,w1",
    "displayName": "Intranet",
    "name": "root",
    "webUrl": "https://contoso.sharepoint.com",
}
DRIVE = {"id": "drive-1", "name": "Documents", "driveType": "documentLibrary"}
FILE = {
    "id": "01F!file",
    "name": "notes.md",
    "size": 64,
    "webUrl": "https://x/notes.md",
    "file": {"mimeType": "text/markdown"},
    "parentReference": {"id": "01F!root"},
}
FOLDER = {
    "id": "01F!folder",
    "name": "Reports",
    "folder": {"childCount": 2},
    "parentReference": {"id": "01F!root"},
}
SPLIST = {
    "id": "list-1",
    "name": "Tasks",
    "displayName": "Tasks",
    "webUrl": "https://x/Lists/Tasks",
    "description": "team tasks",
    "list": {"template": "genericList", "hidden": False},
}
LIST_ITEM = {
    "id": "1",
    "webUrl": "https://x/Lists/Tasks/1",
    "fields": {"@odata.etag": "e1", "Title": "Do thing", "Status": "Open"},
}


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )


def _error(code: str, message: str, status: int) -> httpx.Response:
    return _json({"error": {"code": code, "message": message}}, status)


def handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    body = None
    if request.content and request.headers.get("content-type", "").startswith("application/json"):
        body = json.loads(request.content)
    LOG.append(
        {
            "method": method,
            "path": path,
            "query": dict(request.url.params),
            "body": body,
            "raw": bytes(request.content) if request.content else b"",
        }
    )

    # ── Outlook ──
    if path.endswith(("/me", "/me/")):
        return _json(PROFILE)
    if path.endswith("/me/sendMail"):
        return httpx.Response(202)
    if path.endswith("/me/messages") and method == "POST":
        return _json({**MSG, "id": "draft-1", "subject": body["subject"], "isDraft": True})
    if path.endswith("/me/messages"):
        return _json({"value": [MSG]})
    if "/me/mailFolders/" in path and path.endswith("/messages"):
        return _json({"value": [MSG]})
    if path.endswith("/me/mailFolders"):
        return _json({"value": FOLDERS})
    if "/me/messages/" in path:
        rest = path.split("/me/messages/", 1)[1]
        message_id, _, tail = rest.partition("/")
        if message_id not in (MSG["id"], "draft-1"):
            return _error("ErrorItemNotFound", "The specified object was not found", 404)
        if tail in ("send", "reply", "replyAll", "forward"):
            return httpx.Response(202)
        if tail == "move":
            return _json({**MSG, "id": "AAMk-moved"})
        if tail == "attachments" and method == "POST":
            return _json({**ATT_TXT, "id": "att-new", "name": body["name"]}, 201)
        if tail == "attachments":
            return _json({"value": [ATT_TXT, ATT_PDF]})
        if tail.startswith("attachments/"):
            attachment_id = tail.split("/")[1]
            return _json(ATT_TXT if attachment_id == "att-1" else ATT_PDF)
        if method == "PATCH":
            return _json({**MSG, "isRead": body.get("isRead")})
        if method == "DELETE":
            return httpx.Response(204)
        return _json(MSG)
    if path.endswith("/me/calendarView"):
        return _json({"value": [EVENT]})
    if path.endswith("/me/events") and method == "POST":
        return _json({**EVENT, "id": "ev-new", "subject": body["subject"]}, 201)
    if path.endswith("/me/events"):
        return _json({"value": [EVENT]})
    if "/me/events/" in path:
        tail = path.split("/me/events/", 1)[1].partition("/")[2]
        if tail in ("accept", "decline", "tentativelyAccept"):
            return httpx.Response(202)
        if method == "PATCH":
            return _json({**EVENT, **({"subject": body["subject"]} if "subject" in body else {})})
        if method == "DELETE":
            return httpx.Response(204)
        return _json(EVENT)
    if path.endswith("/me/contacts"):
        return _json({"value": [CONTACT]})

    # ── SharePoint ──
    if path.endswith("/sites"):
        return _json({"value": [SITE]})
    if "/sites/" in path and path.endswith("/drives"):
        return _json({"value": [DRIVE]})
    if "/sites/" in path and path.endswith("/lists"):
        return _json({"value": [SPLIST]})
    if "/sites/" in path and "/lists/" in path and path.endswith("/items"):
        return _json({"value": [LIST_ITEM]})
    if "/sites/" in path:
        return _json(SITE)
    if "/drives/drive-1/search(q=" in path:
        return _json({"value": [FILE]})
    if path.endswith("/drives/drive-1/items/root/children") and method == "GET":
        return _json({"value": [FILE, FOLDER]})
    if path.endswith("/drives/drive-1/root:/Reports:/children"):
        return _json({"value": [FILE]})
    if path.endswith("/drives/drive-1/root:/Reports:"):
        return _json(FOLDER)
    if path.endswith(":/content") and method == "PUT":
        name = path.split(":/")[-2].split("/")[-1]
        return _json({**FILE, "id": "01F!new", "name": name}, 201)
    if path.endswith("/children") and method == "POST":
        return _json({**FOLDER, "id": "01F!newfolder", "name": body["name"]}, 201)
    if path.endswith(f"/items/{FILE['id']}/content"):
        return httpx.Response(
            200, content=b"# Title\nhello world", headers={"Content-Type": "application/octet-stream"}
        )
    for item in (FILE, FOLDER):
        if path.endswith(f"/items/{item['id']}"):
            if method == "PATCH":
                if body.get("name") == "taken.md":
                    return _error("nameAlreadyExists", "The name is already taken", 409)
                return _json({**item, **{k: v for k, v in body.items() if k == "name"}})
            if method == "DELETE":
                return httpx.Response(204)
            return _json(item)
    return _error("itemNotFound", f"no mock for {method} {path}", 404)


class _Credential:
    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("mock-graph-token", 32503680000)

    async def close(self) -> None:
        return None


class _Credentials:
    """Stands in for the on-behalf-of exchange: any assertion gets a token."""

    async def credential_for_user(self, assertion: str, user_id: str) -> _Credential:
        return _Credential()

    async def credential_for_app(self) -> _Credential:
        return _Credential()

    async def close(self) -> None:
        return None


def make_m365() -> M365Client:
    return M365Client(
        M365Settings(tenant_id="t", client_id="c", client_secret="s", max_retries=0),
        credentials=_Credentials(),
        transport=httpx.MockTransport(handler),
    )


def lc(obj: Any) -> Any:
    """Lower-case every key: Kiota serializes action bodies in PascalCase."""
    if isinstance(obj, dict):
        return {k.lower(): lc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lc(v) for v in obj]
    return obj


def last(method: str | None = None, contains: str | None = None) -> dict[str, Any] | None:
    """The most recent logged request matching ``method`` and a path fragment."""
    for entry in reversed(LOG):
        if (method is None or entry["method"] == method) and (
            contains is None or contains in entry["path"]
        ):
            return entry
    return None
