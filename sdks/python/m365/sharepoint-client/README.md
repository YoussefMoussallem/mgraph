# sharepoint-client

Typed SharePoint access over Microsoft Graph, built on
[`m365-client`](../m365-client/). Sites, document libraries, folders and
files (list, resolve by path, search, download, upload, create folders,
move, rename, delete), and lists with their items — paged, error-translated,
and mapped into plain typed models.

| | |
| --- | --- |
| **PyPI name** | `sharepoint-client` |
| **Import** | `sharepoint_client` |
| **Version** | 0.1.0 |
| **Python** | >= 3.11 |
| **Depends on** | `m365-client` (which brings `msgraph-sdk`) |
| **Graph permissions** | Delegated, per operation — see [Permissions](#permissions) |

## Where it sits

`m365-client` deliberately ships no workload code — no `get_site()` —
because that differs per workload. This package is where the SharePoint half
of that lives. It owns *which* Graph calls to make, which fields to
`$select`, how path-based addressing and upload URLs are composed, and how
to map the results; everything else is `m365-client`'s:

| `m365-client` | `sharepoint-client` |
| --- | --- |
| Token acquisition (on-behalf-of, app-only) | `SharePointClient` over a `GraphServiceClient` |
| Credential caching, retry-hardened Graph client | `$select` lists, `root:/{path}:` URL composition, conflict behaviour |
| Error taxonomy and `translate_graph_errors()` | Mapping to `Site`, `Drive`, `DriveItem`, `SharePointList`, `ListItemRecord` |
| `collect()` / `iter_pages()` paging, `MAX_TOP` | Which collections to walk, bounded by that cap |

## Delegated only, and why that matters here

Nothing here acquires a token or builds a Graph client. **Which identity the
calls run as is the caller's decision**, made by choosing which
`GraphServiceClient` to pass in — normally `M365Client.graph_for_user()`, so
Graph returns only the sites and files the signed-in user can already see,
and every upload, move or deletion lands under that user's name in version
history and audit logs.

For SharePoint this is the whole design, not a detail. App-only
`Sites.*.All` is tenant-wide and enterprises reject it; the
`Sites.Selected` alternative scopes app-only access to explicitly granted
sites but needs a per-site grant step — an operational process with an owner.
Delegated access needs none of that, and it means application code is never
the only thing between a caller and every document in the tenant (there is no
RLS backstop here: Graph *is* the store). This package never
references `graph_for_app()` or an application-permission credential type.
Tenant-wide background access — indexing, webhooks — belongs in a separate,
deliberately app-only component.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client -e ./sdks/python/m365/sharepoint-client          # from a clone
```

Published wheels come from the `sharepoint-client-v*` release tag. See
[Python SDK installation](../../docs/installation.md) for the Artifactory feed.

## Quickstart

```python
from m365_client import M365Client, M365Settings
from sharepoint_client import SharePointClient

m365 = M365Client(M365Settings(tenant_id=..., client_id=..., client_secret=...))

# per request: act as the signed-in user
graph = await m365.graph_for_user(user.assertion, user.user_id)
sp = SharePointClient(graph)

sites = await sp.search_sites("intranet")            # or search_sites() for recent sites
drives = await sp.list_drives(sites[0].id)
drive = drives[0]

items = await sp.list_items(drive.id, path="Reports/2026")
file = next(i for i in items if not i.is_folder)
content = await sp.download_item(drive.id, file.id)

hits = await sp.search_files(drive.id, "budget")     # names and indexed content, library-wide

q3 = await sp.create_folder(drive.id, "Q3", parent_path="Reports/2026")
await sp.upload_file(drive.id, name="summary.md", content=b"# Q3\n", parent_item_id=q3.id)
await sp.move_item(drive.id, file.id, new_parent_id=q3.id, new_name="archived.md")
await sp.delete_item(drive.id, file.id)              # to the site recycle bin

lists = await sp.list_lists(sites[0].id)
rows = await sp.get_list_items(sites[0].id, lists[0].id)   # rows[0].fields["Title"]
```

In a FastAPI app built from the
[backend scaffold](../../../../scaffolds/backend/), whose `app/graph.py` builds
`M365Client` in the lifespan and validates the caller's access token,
`get_graph` hands each handler a Graph client acting as the caller:

```python
from app.graph import get_graph
from msgraph import GraphServiceClient
from sharepoint_client import MAX_TOP, SharePointClient

@router.get("/sites")
async def list_sites(
    graph: Annotated[GraphServiceClient, Depends(get_graph)],
    q: str | None = None,
    top: Annotated[int, Query(ge=1, le=MAX_TOP)] = 10,
):
    return await SharePointClient(graph).search_sites(q, top=top)
```

SDK errors should propagate rather than be caught in the handler: the
scaffold's `register_graph_error_handlers` renders each typed error as the
platform envelope. That also preserves Graph's 404-before-403 behaviour for sites the
caller cannot see.

## API

All methods are `async`, act as whoever `graph` acts as, and raise the
[`m365_client` taxonomy](../m365-client/README.md#error-handling) on failure.
Argument problems (an empty id, a name containing `/`, an unknown `conflict`
value, an out-of-range `top`) raise `ValueError` before any network call.

### Sites and libraries

| Method | Returns | Notes |
| --- | --- | --- |
| `search_sites(query=None, *, top=10)` | `list[Site]` | `None`/blank sends Graph's `*` wildcard (recently active sites). |
| `get_site(site_id)` | `Site` | 404 (`GraphNotFoundError`) when missing *or invisible*. |
| `list_drives(site_id, *, top=10)` | `list[Drive]` | Document libraries, with owner and quota. |

### Files and folders

| Method | Returns | Notes |
| --- | --- | --- |
| `list_items(drive_id, *, path=None, item_id=None, top=10)` | `list[DriveItem]` | Children of the root, of a folder by `path`, or of a folder by `item_id`. Both together is a `ValueError`. |
| `get_item(drive_id, item_id)` | `DriveItem` | One file or folder. |
| `get_item_by_path(drive_id, path)` | `DriveItem` | Resolves `Reports/2026/Q3.md` to its item — and so its id — without walking the tree. |
| `download_item(drive_id, item_id)` | `bytes` | Fully buffered by the generated SDK — documents, not multi-gigabyte files. `GraphNotFoundError` for folders. |
| `search_files(drive_id, query, *, top=10)` | `list[DriveItem]` | Graph's drive search: file names and indexed content, across the whole library. |
| `upload_file(drive_id, *, name, content, parent_item_id=None, parent_path=None, conflict="replace")` | `DriveItem` | Simple PUT into the root, a folder by id, or a folder by path (not both). At most `MAX_UPLOAD_BYTES` (4 MB, Graph's single-request limit); larger raises `ValueError` — bigger files need an upload session, which is out of scope here. |
| `create_folder(drive_id, name, *, parent_item_id=None, parent_path=None, conflict="fail")` | `DriveItem` | In the root, a folder by id, or a folder by path. |
| `move_item(drive_id, item_id, *, new_parent_id=None, new_name=None)` | `DriveItem` | Move, rename, or both within the drive; neither is a `ValueError`. `DriveItem.parent_id` from any listing is a ready-made destination. |
| `delete_item(drive_id, item_id)` | `None` | To the site recycle bin, recoverable by the user or a site admin. |

`conflict` decides what happens when the name is taken: `fail` (Graph
returns 409 → `GraphConflictError`), `replace` (overwrite — a new version in
libraries with versioning on), or `rename` (Graph appends a counter, e.g.
`report 1.md`). Uploads default to `replace`, folders to `fail`.

### Lists

| Method | Returns | Notes |
| --- | --- | --- |
| `list_lists(site_id, *, top=10)` | `list[SharePointList]` | Every list the user can see — document libraries and hidden system lists included; filter on `template` and `hidden`. |
| `get_list_items(site_id, list_id, *, top=10)` | `list[ListItemRecord]` | Items with their `fields`: column internal names → values, `@odata` noise stripped. |

### Rules worth knowing

`top` is a **total cap**, not a page size: Graph pages are followed via
`collect()` until `top` items are collected or the collection runs out. It
must be between 1 and `MAX_TOP` (50), matching the platform rule that no list
endpoint returns more than 50 per call. A consumer that genuinely needs a
bigger walk streams with `m365_client.iter_pages` against
`SharePointClient.graph`, which is public for exactly that reason.

`path` segments are percent-encoded by the client (`/` stays the separator),
so folder names with spaces or `#` are safe to pass as-is. Names given to
`upload_file`, `create_folder` and `move_item` are single segments: a `/`
in them is a `ValueError`, not an implicit folder.

Path-addressed writes resolve the parent folder to its id first, so a
misspelled `parent_path` fails with `GraphNotFoundError` before anything is
written.

### Models

Frozen dataclasses (no pydantic, matching `m365-client`). Every field except
`id` is optional. Timestamps are timezone-aware `datetime`s. They serialise
through `dataclasses.asdict` and work directly as FastAPI `response_model`
types.

```
Site(id, display_name, name, web_url, created_at, last_modified_at)
Drive(id, name, drive_type, web_url, created_at, last_modified_at, owner: Identity, quota_used, quota_total)
DriveItem(id, name, size, created_at, last_modified_at, web_url, mime_type, is_folder, child_count,
          created_by: Identity, last_modified_by: Identity, parent_id)
Identity(display_name, email)
SharePointList(id, name, display_name, web_url, description, template, hidden)
ListItemRecord(id, web_url, created_at, last_modified_at, fields: dict[str, Any])
```

Ids are opaque and must be treated as such: site ids contain commas, drive
item ids contain `!` and `.`. `Identity.email` is read from Kiota's
`additional_data`, because the generated `Identity` model has no `email`
field even though real responses carry one — a value that was silently
dropped once before. `ListItemRecord.fields` comes from the same place: list
columns are tenant-defined, so there is no typed model to map them into.

## Permissions

Graph checks the delegated permission per call, so grant only what the app
actually calls — an app registered with the read permissions alone gets a
`GraphAuthError` from `upload_file`, never a silent no-op.

| Operations | Delegated permissions |
| --- | --- |
| Sites, drives, files, search, lists | `Sites.Read.All`, `Files.Read.All` |
| Upload, create folder, move, rename, delete | `Sites.ReadWrite.All`, `Files.ReadWrite.All` |

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

- [`outlook-client`](../outlook-client/) — the sibling workload SDK.
- [`m365-langchain-tools`](../langchain-tools/) — this client as LangChain agent tools.
- [`m365-client`](../m365-client/) — the foundation, and the place to read
  about on-behalf-of, credential caching, and the token contract that trips
  every team once.
