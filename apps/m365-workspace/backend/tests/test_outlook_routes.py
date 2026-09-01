"""Outlook routes through the full app, against the mock Graph."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from tests.conftest import SCOPE, make_token
from tests.graph_mock import last, lc

BASE = "/api/v1/outlook"


# ── Auth gate ────────────────────────────────────────────────────────


def test_requires_a_token(workspace_app) -> None:
    res = TestClient(workspace_app).get(f"{BASE}/messages")
    assert res.status_code == 401
    assert res.json()["code"] == "unauthorized"


def test_id_token_is_refused_with_the_fix(workspace_app) -> None:
    res = TestClient(workspace_app).get(
        f"{BASE}/messages", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert res.status_code == 401
    assert SCOPE in res.json()["detail"]


# ── Profile, folders, messages ───────────────────────────────────────


def test_profile(api: TestClient) -> None:
    res = api.get(f"{BASE}/profile")
    assert res.status_code == 200
    assert res.json()["id"] == "oid-1234"


def test_folders(api: TestClient) -> None:
    res = api.get(f"{BASE}/folders")
    assert res.status_code == 200
    assert res.json()[0]["display_name"] == "Inbox"


def test_list_messages_with_search(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages", params={"search": "invoice", "top": 5})
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["from_address"] == "ada@x.com"
    assert body[0]["has_attachments"] is True
    assert last("GET", "/me/messages")["query"]["$search"] == '"invoice"'


def test_search_and_unread_only_is_a_400(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages", params={"search": "x", "unread_only": "true"})
    assert res.status_code == 400
    assert res.json()["code"] == "bad_request"


def test_top_is_capped_at_50(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages", params={"top": 500})
    assert res.status_code == 422


def test_get_message(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages/AAMk-1")
    assert res.status_code == 200
    body = res.json()
    assert body["body_content"] == "Body text"
    assert body["cc_recipients"][0]["address"] == "cc@x.com"


def test_unknown_message_is_a_graph_404(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages/nope")
    assert res.status_code == 404
    assert res.json()["code"] == "graph_not_found"


def test_send_message(api: TestClient) -> None:
    res = api.post(
        f"{BASE}/messages/send",
        json={"to": ["b@x.com"], "cc": ["c@x.com"], "subject": "Hi", "body": "Body"},
    )
    assert res.status_code == 202
    assert res.json() == {"status": "sent"}
    sent = lc(last("POST", "/me/sendMail")["body"])
    assert sent["message"]["subject"] == "Hi"
    assert sent["message"]["torecipients"][0]["emailaddress"]["address"] == "b@x.com"
    assert sent["savetosentitems"] is True


def test_send_to_a_bad_address_is_a_400_with_the_sdk_message(api: TestClient) -> None:
    res = api.post(f"{BASE}/messages/send", json={"to": ["nope"], "subject": "Hi", "body": "B"})
    assert res.status_code == 400
    assert res.json()["code"] == "bad_request"
    assert "email" in res.json()["detail"]
    assert last("POST", "/me/sendMail") is None


def test_draft_then_attach_then_send(api: TestClient) -> None:
    draft = api.post(f"{BASE}/messages/drafts", json={"subject": "D", "body": "b", "to": ["b@x.com"]})
    assert draft.status_code == 201
    draft_id = draft.json()["id"]
    assert draft_id == "draft-1"

    attached = api.post(
        f"{BASE}/messages/{draft_id}/attachments",
        files={"file": ("a.txt", b"xyz", "text/plain")},
    )
    assert attached.status_code == 201
    assert attached.json()["name"] == "a.txt"
    posted = last("POST", "/attachments")["body"]
    assert posted["contentBytes"] == base64.b64encode(b"xyz").decode()

    sent = api.post(f"{BASE}/messages/{draft_id}/send")
    assert sent.status_code == 202
    assert last("POST", "/send") is not None


def test_reply_all_and_forward(api: TestClient) -> None:
    res = api.post(f"{BASE}/messages/AAMk-1/reply", json={"comment": "ok", "reply_all": True})
    assert res.status_code == 202
    assert lc(last("POST", "/replyAll")["body"])["comment"] == "ok"

    res = api.post(f"{BASE}/messages/AAMk-1/forward", json={"to": ["f@x.com"], "comment": "fyi"})
    assert res.status_code == 202
    forwarded = lc(last("POST", "/forward")["body"])
    assert forwarded["torecipients"][0]["emailaddress"]["address"] == "f@x.com"


def test_move_returns_the_new_id(api: TestClient) -> None:
    res = api.post(f"{BASE}/messages/AAMk-1/move", json={"destination_folder": "archive"})
    assert res.status_code == 200
    assert res.json()["id"] == "AAMk-moved"
    assert lc(last("POST", "/move")["body"])["destinationid"] == "archive"


def test_delete_defaults_to_deleted_items(api: TestClient) -> None:
    res = api.delete(f"{BASE}/messages/AAMk-1")
    assert res.status_code == 204
    assert lc(last("POST", "/move")["body"])["destinationid"] == "deleteditems"

    res = api.delete(f"{BASE}/messages/AAMk-1", params={"permanent": "true"})
    assert res.status_code == 204
    assert last("DELETE", "/me/messages/") is not None


def test_mark_unread(api: TestClient) -> None:
    res = api.patch(f"{BASE}/messages/AAMk-1/read", json={"read": False})
    assert res.status_code == 204
    patched = last("PATCH", "/me/messages/")["body"]
    assert patched["isRead"] is False


# ── Attachments ──────────────────────────────────────────────────────


def test_list_and_download_attachments(api: TestClient) -> None:
    res = api.get(f"{BASE}/messages/AAMk-1/attachments")
    assert res.status_code == 200
    assert [a["name"] for a in res.json()] == ["notes.txt", "deck.pdf"]

    res = api.get(f"{BASE}/messages/AAMk-1/attachments/att-1/content")
    assert res.status_code == 200
    assert res.content == b"hello notes"
    assert res.headers["content-type"].startswith("text/plain")
    assert 'filename="notes.txt"' in res.headers["content-disposition"]


# ── Calendar ─────────────────────────────────────────────────────────


def test_calendar_view_window(api: TestClient) -> None:
    res = api.get(
        f"{BASE}/events", params={"start": "2026-09-01T00:00:00", "end": "2026-09-07T00:00:00"}
    )
    assert res.status_code == 200
    event = res.json()[0]
    assert event["online_meeting_url"] == "https://teams/join"
    assert event["attendees"][0]["response"] == "accepted"
    query = last("GET", "/me/calendarView")["query"]
    assert query["startDateTime"] == "2026-09-01T00:00:00"


def test_events_without_window_are_upcoming(api: TestClient) -> None:
    res = api.get(f"{BASE}/events")
    assert res.status_code == 200
    assert last("GET", "/me/events")["query"]["$filter"].startswith("start/dateTime ge '20")


def test_half_a_window_is_a_400(api: TestClient) -> None:
    res = api.get(f"{BASE}/events", params={"start": "2026-09-01T00:00:00"})
    assert res.status_code == 400


def test_create_update_respond_delete_event(api: TestClient) -> None:
    created = api.post(
        f"{BASE}/events",
        json={
            "subject": "Sync",
            "start": "2026-09-03T14:00:00",
            "end": "2026-09-03T15:00:00",
            "time_zone": "Europe/London",
            "attendees": ["b@x.com"],
            "online_meeting": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["id"] == "ev-new"
    posted = last("POST", "/me/events")["body"]
    assert posted["start"] == {"dateTime": "2026-09-03T14:00:00", "timeZone": "Europe/London"}
    assert posted["isOnlineMeeting"] is True

    updated = api.patch(f"{BASE}/events/ev-1", json={"subject": "New"})
    assert updated.status_code == 200
    assert updated.json()["subject"] == "New"

    responded = api.post(f"{BASE}/events/ev-1/respond", json={"response": "decline", "comment": "no"})
    assert responded.status_code == 204
    assert lc(last("POST", "/decline")["body"])["comment"] == "no"

    deleted = api.delete(f"{BASE}/events/ev-1")
    assert deleted.status_code == 204
    assert last("DELETE", "/me/events/") is not None


# ── Contacts ─────────────────────────────────────────────────────────


def test_contacts_prefix_filter(api: TestClient) -> None:
    res = api.get(f"{BASE}/contacts", params={"name_starts_with": "Ada"})
    assert res.status_code == 200
    assert res.json()[0]["email_addresses"] == ["ada@x.com"]
    assert last("GET", "/me/contacts")["query"]["$filter"] == "startswith(displayName,'Ada')"
