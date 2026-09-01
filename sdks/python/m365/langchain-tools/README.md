# m365-langchain-tools

Outlook and SharePoint as **LangChain agent tools** — 24 LLM-callable tools
(13 reads, 11 writes) adapted from [`outlook-client`](../outlook-client/) and
[`sharepoint-client`](../sharepoint-client/), for any LangChain-based agent
host (Apex/Agent Studio included). Writes are opt-out, so a read-only agent
is one flag away.

| | |
| --- | --- |
| **PyPI name** | `m365-langchain-tools` |
| **Import** | `m365_langchain_tools` |
| **Version** | 0.1.0 |
| **Python** | >= 3.11 |
| **Depends on** | `m365-client`, `outlook-client`, `sharepoint-client`, `langchain-core` |
| **Graph permissions** | Delegated, per tool group — see [Permissions](#permissions) |

## Where it sits

The SDKs stay platform-generic; this package is the **agent-framework
adapter layer** on top of them, the way the backend scaffold is the FastAPI
layer. It owns only what an LLM tool needs — stable names, routing
descriptions, small validated schemas, bounded JSON results, and the
side-effect warnings a model must see before it sends something — and
imports nothing from any specific host.

| The SDKs | `m365-langchain-tools` |
| --- | --- |
| Which Graph calls, `$select`, paging, error taxonomy | `BaseTool` classes with names and when/when-not descriptions |
| Typed models (`MessageSummary`, `DriveItem`, …) | Pydantic input schemas, re-validated inside `_arun()` |
| Token exchange and credential caching (`m365-client`) | The `graph_provider` seam the host binds per execution |

## The tools

### Outlook

| Tool | Does | Arguments | Side effect |
| --- | --- | --- | --- |
| `list_outlook_messages` | Newest messages, or `$search` hits by relevance: subject, sender, date, preview — no bodies | `top`, `folder`, `unread_only`, `search` | — |
| `get_outlook_message` | One full email, body truncated to `max_chars`, plus its attachment list | `message_id`, `max_chars` | — |
| `list_outlook_folders` | Mail folders with unread counts | `top` | — |
| `read_outlook_attachment` | A text-format attachment's content; refuses binaries with the file's type | `message_id`, `attachment_id`, `max_chars` | — |
| `list_outlook_events` | Calendar view for a window (recurrences expanded), or upcoming events | `start`, `end`, `top` | — |
| `list_outlook_contacts` | Personal contacts, prefix-matched on name — for resolving addresses | `name_starts_with`, `top` | — |
| `send_outlook_message` | Sends a new email now | `to`, `subject`, `body`, `cc`, `bcc`, `body_type` | **sends mail** |
| `create_outlook_draft` | Saves a draft for a person to review and send | `subject`, `body`, `to`, `cc`, `bcc`, `body_type` | writes Drafts |
| `reply_outlook_message` | Replies, or reply-all, to a message | `message_id`, `comment`, `reply_all` | **sends mail** |
| `forward_outlook_message` | Forwards a message with its attachments | `message_id`, `to`, `comment` | **sends mail** |
| `move_outlook_message` | Moves to a folder (`archive`, `deleteditems`, an id) and returns the new id | `message_id`, `destination_folder` | moves mail |
| `create_outlook_event` | Creates an event, inviting attendees; optional Teams link | `subject`, `start`, `end`, `time_zone`, `attendees`, `location`, `body`, `online_meeting` | **sends invitations** |
| `respond_outlook_event` | Accept / decline / tentative, with an optional comment | `event_id`, `response`, `comment` | **notifies the organizer** |

### SharePoint

| Tool | Does | Arguments | Side effect |
| --- | --- | --- | --- |
| `search_sharepoint_sites` | Sites the user can see — the SharePoint entry point | `query`, `top` | — |
| `list_sharepoint_drives` | A site's document libraries | `site_id`, `top` | — |
| `list_sharepoint_files` | Files/folders in a drive root, a `path`, or a folder `item_id` | `drive_id`, `path`, `item_id`, `top` | — |
| `search_sharepoint_files` | Library-wide search by file name and indexed content | `drive_id`, `query`, `top` | — |
| `read_sharepoint_file` | Text-format file content (md, csv, json, xml, code…); refuses docx/xlsx/pdf with the reason | `drive_id`, `item_id`, `max_chars` | — |
| `list_sharepoint_lists` | A site's lists, with template and hidden flag | `site_id`, `top` | — |
| `get_sharepoint_list_items` | List items with their column values | `site_id`, `list_id`, `top` | — |
| `upload_sharepoint_file` | Writes a UTF-8 text file into the root, a `parent_path`, or a `parent_item_id` | `drive_id`, `name`, `content`, `parent_path`, `parent_item_id`, `conflict` | creates a file (`conflict` defaults to `rename`, so nothing is overwritten unless asked) |
| `create_sharepoint_folder` | Creates a folder; an existing name is a conflict, not a silent reuse | `drive_id`, `name`, `parent_path`, `parent_item_id` | creates a folder |
| `move_sharepoint_item` | Moves and/or renames a file or folder | `drive_id`, `item_id`, `new_parent_id`, `new_name` | moves |
| `delete_sharepoint_item` | Sends a file or folder to the site recycle bin | `drive_id`, `item_id` | deletes (recoverable) |

All `async`. Reads are safe to repeat; every write's description tells the
model what happens in the world when it is called, and points to the
reversible alternative where one exists (draft over send, recycle bin over
permanent delete — nothing here deletes permanently). Recoverable failures
come back as **actionable text** the model can correct (wrong id → "copy it
from an earlier result"; 409 → "choose another name"; 429 → the
`Retry-After`; 403 → "permission not granted, don't retry"); infrastructure
failures raise, so the host records a failed tool call. Results are
`json.dumps`-serialized strings — never bare dicts, which some hosts render
as Python reprs.

## Identity: the `graph_provider` seam

**The model never chooses who it acts as.** Every tool is constructed with an
async factory the host builds from trusted runtime context:

```python
from m365_client import M365Client, M365Settings
from m365_langchain_tools import m365_tools  # or outlook_tools / sharepoint_tools

m365 = M365Client(M365Settings(tenant_id=..., client_id=..., client_secret=...))  # once per process

# per agent execution — assertion is the signed-in user's access token for
# YOUR app's own API scope, taken from the authenticated request context
def graph_provider():
    return m365.graph_for_user(assertion, user_oid)

tools = m365_tools(graph_provider)                        # all 24
readers = m365_tools(graph_provider, include_writes=False)  # the 13 reads
llm_with_tools = llm.bind_tools(tools)
```

With a `graph_for_user` provider, Graph enforces the signed-in user's own
permissions — the tools cannot read anything the user could not, and every
email sent or file uploaded lands under that user's name. The provider
field is excluded from serialization, so it never appears in the
provider-facing schema or a trace payload.

**The token prerequisite** (decide this before wiring an agent host): the
on-behalf-of exchange needs the user's **access token for the host's own API
scope** — an ID token will not work, and a bare user id is not enough. A host
whose executions outlive the user's session needs its own answer (capture
the assertion at kickoff and respect its ~60–90 min lifetime, or a stored
refresh-token flow via `m365-client`'s `CredentialProvider` seam). App-only
(`m365.graph_for_app()`) also fits the seam but grants tenant-wide access —
and with the write tools, tenant-wide writes — a deliberate decision, not a
default.

## Writes and agents

`include_writes` is the coarse switch; `outlook_tools()` and
`sharepoint_tools()` take it too, so a mail-reading, file-writing agent is a
two-line composition. Beyond that, the host decides how much autonomy a
write gets:

- Agents that only need to *look* get `include_writes=False`. There is no
  reason to hand a summarizer a send button.
- `send_outlook_message`, `reply_outlook_message`, `forward_outlook_message`,
  `create_outlook_event` and `respond_outlook_event` reach other people and
  cannot be undone. Put them behind the host's approval step (a LangGraph
  interrupt, an Apex human-in-the-loop node) or leave them out and bind
  `create_outlook_draft` instead — the person sends from Outlook.
- SharePoint writes stay inside the tenant and are recoverable
  (`rename`-by-default uploads, recycle-bin deletes), which is why they are
  the safer half to grant an autonomous agent.

## Wiring into Apex (Agent Studio)

Per the Apex tool guide, these are **per-agent stateful tools** — construct
them in `AgentNode._get_tools()`, never as registry singletons:

```python
# app/workflow/nodes/agent.py::AgentNode._get_tools()
if config.get("enableM365Tools"):
    assertion, user_oid = ...  # from trusted workflow/auth context — never from the model
    tools.extend(
        m365_tools(
            lambda: m365.graph_for_user(assertion, user_oid),
            include_writes=config.get("enableM365Writes", False),
        )
    )
```

Checklist mapping:

- Fresh instances per execution (the factories guarantee this) — no
  cross-request state leakage; bind in **both** `agent_standard_mode.py`
  and `agent_multi_loop.py` paths if context injection is added there.
- Arguments are re-validated inside `_arun()`, because Apex executors call
  `_arun` directly and bypass `ainvoke()`'s Pydantic pass.
- Results are JSON strings; no custom result protocol, so no executor or
  frontend changes are needed.
- Selection: add palette toggles (`enableM365Tools`, `enableM365Writes`)
  plus injection in `AgentNode.execute()`, or place the tool names straight
  into `config.tools`.
- Names are stable and snake_case: treat renames as breaking saved
  workflows.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client -e ./sdks/python/m365/outlook-client \
  -e ./sdks/python/m365/sharepoint-client -e ./sdks/python/m365/langchain-tools   # from a clone
```

Published wheels come from the `m365-langchain-tools-v*` release tag. See
[Python SDK installation](../../docs/installation.md) for the Artifactory feed.

## Permissions

Delegated, and granted per tool group the agent actually binds:

| Tools | Delegated permissions |
| --- | --- |
| Outlook reads | `User.Read`, `Mail.Read`, `Calendars.Read`, `Contacts.Read` |
| Outlook writes | plus `Mail.Send` (send, reply, forward), `Mail.ReadWrite` (drafts, move), `Calendars.ReadWrite` (create, respond) |
| SharePoint reads | `Sites.Read.All`, `Files.Read.All` |
| SharePoint writes | `Sites.ReadWrite.All`, `Files.ReadWrite.All` |

A missing permission surfaces as "Microsoft Graph denied access … Retrying
with the same arguments will not help", so the model stops rather than
looping.

## Bounds

`top` is capped at 50 everywhere (the platform list rule, enforced before any
network call); message bodies, attachment text and file text are truncated to
`max_chars` (default 20,000, max 50,000) with a `truncated`/`body_truncated`
flag; file and attachment reads refuse anything without a text MIME type, and
file reads anything over 5 MB; uploads take text only, at most 4 MB after
UTF-8 encoding. Every description tells the model where its IDs come from,
because Graph IDs are opaque and cannot be guessed — and that a moved message
gets a new one.

## Related

- [`outlook-client`](../outlook-client/) / [`sharepoint-client`](../sharepoint-client/) — the SDKs these adapt.
- [`m365-client`](../m365-client/) — auth, caching, errors, paging; the place
  to read about the on-behalf-of token contract.
