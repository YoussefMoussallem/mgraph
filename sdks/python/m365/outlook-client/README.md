# outlook-client

Typed Outlook access over Microsoft Graph, built on
[`m365-client`](../m365-client/). Mail (read, search, send, reply, forward,
drafts, attachments, move, delete), calendar (list, create, update, respond,
delete), contacts, and the signed-in user's profile — paged,
error-translated, and mapped into plain typed models.

| | |
| --- | --- |
| **PyPI name** | `outlook-client` |
| **Import** | `outlook_client` |
| **Version** | 0.1.0 |
| **Python** | >= 3.11 |
| **Depends on** | `m365-client` (which brings `msgraph-sdk`) |
| **Graph permissions** | Delegated, per operation — see [Permissions](#permissions) |

## Where it sits

`m365-client` deliberately ships no workload code — no `list_messages()` —
because that differs per workload. This package is where the Outlook half of
that lives. It owns *which* Graph calls to make, which fields to `$select`,
how request bodies are composed, and how to map the results; everything else
is `m365-client`'s:

| `m365-client` | `outlook-client` |
| --- | --- |
| Token acquisition (on-behalf-of, app-only) | `OutlookClient` over a `GraphServiceClient` |
| Credential caching, retry-hardened Graph client | `$select` lists, `$search`/`$filter`/`$orderby` rules for messages |
| Error taxonomy and `translate_graph_errors()` | Mapping to `MessageSummary`, `Event`, `Contact`, … and building `sendMail`, event and attachment bodies |
| `collect()` / `iter_pages()` paging, `MAX_TOP` | Which collections to walk, bounded by that cap |

Nothing here acquires a token or builds a Graph client. **Which identity the
calls run as is the caller's decision**, made by choosing which
`GraphServiceClient` to pass in — normally `M365Client.graph_for_user()`, so
Graph enforces the signed-in user's own permissions, and every email sent or
event created lands under that user's name.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client -e ./sdks/python/m365/outlook-client          # from a clone
```

Published wheels come from the `outlook-client-v*` release tag. See
[Python SDK installation](../../docs/installation.md) for the Artifactory feed.

## Quickstart

```python
from m365_client import M365Client, M365Settings
from outlook_client import OutlookClient

m365 = M365Client(M365Settings(tenant_id=..., client_id=..., client_secret=...))

# per request: act as the signed-in user
graph = await m365.graph_for_user(user.assertion, user.user_id)
outlook = OutlookClient(graph)

# mail
unread = await outlook.list_messages(top=10, unread_only=True)
detail = await outlook.get_message(unread[0].id)
await outlook.reply_message(detail.id, "Thanks — on it.")
archived = await outlook.move_message(detail.id, "archive")      # note: new id

invoices = await outlook.list_messages(search="subject:invoice hasAttachments:true")
files = await outlook.list_attachments(invoices[0].id)
pdf = await outlook.download_attachment(invoices[0].id, files[0].id)   # .content is bytes

# calendar
week = await outlook.list_events(start="2026-09-01T00:00:00", end="2026-09-08T00:00:00")
await outlook.create_event(
    subject="Sync", start="2026-09-03T14:00:00", end="2026-09-03T14:30:00",
    time_zone="Europe/London", attendees=["b@contoso.com"], online_meeting=True,
)

# contacts, profile
people = await outlook.list_contacts(name_starts_with="Ada")
me = await outlook.get_profile()
```

In a FastAPI app built from the
[backend scaffold](../../../../scaffolds/backend/), whose `app/graph.py` builds
`M365Client` in the lifespan and validates the caller's access token,
`get_graph` hands each handler a Graph client acting as the caller:

```python
from app.graph import get_graph
from msgraph import GraphServiceClient
from outlook_client import MAX_TOP, OutlookClient

@router.get("/messages")
async def list_messages(
    graph: Annotated[GraphServiceClient, Depends(get_graph)],
    top: Annotated[int, Query(ge=1, le=MAX_TOP)] = 10,
):
    return await OutlookClient(graph).list_messages(top=top)
```

SDK errors should propagate rather than be caught in the handler: the
scaffold's `register_graph_error_handlers` renders each typed error as the
platform envelope with a distinct `code`.

## API

All methods are `async`, act as whoever `graph` acts as, and raise the
[`m365_client` taxonomy](../m365-client/README.md#error-handling) on failure.
Argument problems (an empty id, a malformed address, an out-of-range `top`)
raise `ValueError` before any network call.

### Mail

| Method | Returns | Notes |
| --- | --- | --- |
| `list_messages(*, top=10, folder=None, unread_only=False, search=None)` | `list[MessageSummary]` | Newest first, no bodies. `folder` takes a well-known name (`inbox`, `sentitems`, `drafts`, `deleteditems`, `archive`, …) or an id from `list_folders()`. `search` is Graph `$search` — free text or `from:`, `subject:`, `hasAttachments:true` — returned by relevance; it cannot be combined with `unread_only` (`ValueError`). |
| `get_message(message_id)` | `MessageDetail` | Full body plus to/cc recipients. `body_content_type` is the wire value (`text`/`html`). |
| `list_folders(*, top=20)` | `list[MailFolder]` | Top-level folders with item and unread counts. |
| `send_message(*, subject, body, to, cc=(), bcc=(), body_type="text", save_to_sent=True)` | `None` | `sendMail`: sends immediately. **Not idempotent** — two calls send two emails. |
| `create_draft(*, subject, body, to=(), cc=(), bcc=(), body_type="text")` | `MessageDetail` | Saved to Drafts and returned with its id and `web_link`. Attach with `add_attachment`, send with `send_draft`. |
| `send_draft(message_id)` | `None` | Sends an existing draft. |
| `reply_message(message_id, comment, *, reply_all=False)` | `None` | Graph composes the reply (quoted original, recipients) and sends it. |
| `forward_message(message_id, *, to, comment="")` | `None` | Forwards with the original's attachments. |
| `move_message(message_id, destination_folder)` | `MessageDetail` | Returns the message **under its new id** — Graph re-ids on move and the old id stops working. |
| `delete_message(message_id, *, permanent=False)` | `None` | Default moves to Deleted Items (recoverable). `permanent=True` hard-deletes. |
| `set_read(message_id, *, read=True)` | `None` | Mark read or unread. |

### Attachments

| Method | Returns | Notes |
| --- | --- | --- |
| `list_attachments(message_id, *, top=50)` | `list[Attachment]` | Metadata only: name, MIME type, size, inline flag. |
| `download_attachment(message_id, attachment_id)` | `AttachmentContent` | Metadata plus bytes, fully buffered. File attachments only — an attached Outlook item or cloud-file reference raises `GraphInvalidRequestError` rather than returning empty bytes. |
| `add_attachment(message_id, *, name, content, content_type="application/octet-stream")` | `Attachment` | Attaches bytes to a draft. At most `MAX_ATTACHMENT_BYTES` (3 MB, Graph's single-request limit); larger raises `ValueError`. |

### Calendar

| Method | Returns | Notes |
| --- | --- | --- |
| `list_events(*, start=None, end=None, top=10)` | `list[Event]` | Ordered by start. With both bounds it uses Graph's calendar view, which expands recurring series into the occurrences inside the window; without, upcoming events from now on — a recurring series counts only if the series itself starts in the future, so use a window for what is really on the calendar. One bound alone is a `ValueError`. |
| `get_event(event_id)` | `Event` | One event with organizer, attendees and their responses. |
| `create_event(*, subject, start, end, time_zone="UTC", body=None, body_type="text", attendees=(), location=None, is_all_day=False, online_meeting=False)` | `Event` | Attendees receive invitations. `online_meeting=True` adds a Teams link (`online_meeting_url`). |
| `update_event(event_id, *, subject=None, start=None, end=None, time_zone="UTC", body=None, body_type="text", location=None)` | `Event` | Patches only the fields given; attendees receive the update. |
| `respond_event(event_id, response, *, comment=None, send_response=True)` | `None` | `response` is `accept`, `decline` or `tentative`. |
| `delete_event(event_id)` | `None` | As organizer, attendees get a cancellation; as attendee, it leaves the user's calendar only. |

### Contacts and profile

| Method | Returns | Notes |
| --- | --- | --- |
| `list_contacts(*, top=10, name_starts_with=None)` | `list[Contact]` | The user's personal contacts by display name; `name_starts_with` is a `startswith(displayName, …)` filter. |
| `get_profile()` | `UserProfile` | `/me`. Its `id` equals the caller's `oid` claim when on-behalf-of is wired correctly. |

### Rules worth knowing

`top` is a **total cap**, not a page size: Graph pages are followed via
`collect()` until `top` items are collected or the collection runs out.
It must be between 1 and `MAX_TOP` (50), matching the platform rule that no
list endpoint returns more than 50 per call. A consumer that genuinely needs
a bigger walk streams with `m365_client.iter_pages` against
`OutlookClient.graph`, which is public for exactly that reason.

`unread_only` builds the `$filter` so that it satisfies Graph's rule for
combining `$filter` with `$orderby` on messages (the ordered property has to
lead the filter), which otherwise fails with `InefficientFilter`. `$search`
cannot be combined with either, which is why `search` and `unread_only` are
mutually exclusive.

**Times.** Graph pairs a zone-less wall-clock time with a separate zone name.
`start`/`end` on writes accept ISO 8601 text or a `datetime`: text is passed
through as wall-clock time in `time_zone` (Graph accepts IANA and Windows
names — `Europe/London`, `W. Europe Standard Time`); an aware `datetime` is
only unambiguous in UTC, so it is converted and `time_zone` must be `"UTC"`.
`Event.start`/`Event.end` come back as Graph's wall-clock strings in
`Event.time_zone`, unconverted. For `list_events` the bounds are UTC when
they carry no offset.

### Models

Frozen dataclasses (no pydantic, matching `m365-client`). Every field except
`id` is optional, because tenants differ in what they populate. Timestamps
are timezone-aware `datetime`s, except event times (see above). They
serialise through `dataclasses.asdict` and work directly as FastAPI
`response_model` types.

```
Recipient(name, address)
MessageSummary(id, subject, from_name, from_address, received_at, is_read, body_preview,
               has_attachments, web_link)
MessageDetail(MessageSummary + body_content, body_content_type,
              to_recipients: tuple[Recipient, ...], cc_recipients: tuple[Recipient, ...])
MailFolder(id, display_name, total_item_count, unread_item_count)
Attachment(id, name, content_type, size, is_inline)
AttachmentContent(attachment: Attachment, content: bytes)
Attendee(name, address, type, response)
Event(id, subject, start, end, time_zone, is_all_day, is_cancelled, location, organizer: Recipient,
      attendees: tuple[Attendee, ...], is_online_meeting, online_meeting_url, web_link,
      body_preview, response_status)
Contact(id, display_name, given_name, surname, email_addresses: tuple[str, ...], company_name,
        job_title, mobile_phone, business_phones: tuple[str, ...])
UserProfile(id, display_name, mail, user_principal_name, job_title, office_location)
```

## Permissions

Graph checks the delegated permission per call, so grant only what the app
actually calls — a read-only app registered with `Mail.Read` alone gets a
`GraphAuthError` from `send_message`, never a silent no-op.

| Operations | Delegated permission |
| --- | --- |
| Profile | `User.Read` |
| Read messages, folders, attachments | `Mail.Read` |
| Drafts, add attachment, move, delete, mark read | `Mail.ReadWrite` |
| Send, reply, forward | `Mail.Send` |
| List and read events | `Calendars.Read` |
| Create, update, respond to, delete events | `Calendars.ReadWrite` |
| Contacts | `Contacts.Read` |

## Against a real tenant

Nothing here needs a tenant to build or test against. For a first end-to-end
run — an app registration with an exposed API scope, admin-consented delegated
permissions, and a caller that sends an access token rather than an ID token —
see [Trying it against a real tenant](../README.md#trying-it-against-a-real-tenant)
in the family README.

## Dependencies

| Package | Why |
| --- | --- |
| `m365-client` | Everything this package does not do: auth, caching, the configured Graph client, error translation, paging. Brings `msgraph-sdk`, whose generated request builders and models are used here. |

No others. The FastAPI glue — validating the caller's token, `get_graph`, error mapping — lives in the backend scaffold's `app/graph.py`; this package adds one constructor call on top of it.

## Related

- [`sharepoint-client`](../sharepoint-client/) — the sibling workload SDK.
- [`m365-langchain-tools`](../langchain-tools/) — this client as LangChain agent tools.
- [`m365-client`](../m365-client/) — the foundation, and the place to read
  about on-behalf-of, credential caching, and the token contract that trips
  every team once.
