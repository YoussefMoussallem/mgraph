# sharepoint-client

Typed SharePoint access over Microsoft Graph, built on
[m365-client](../m365-client/index.md). Sites, document libraries, folders
and files (list, resolve by path, search, download, upload, create folders,
move, rename, delete), and lists with their items — paged, error-translated,
and mapped into plain typed models.

| | |
|---|---|
| **Import** | `sharepoint_client` |
| **Depends on** | `m365-client` |
| **Graph permissions** | Delegated, per operation — see [Permissions](#permissions) |

## Where it sits

`m365-client` owns what every Microsoft 365 integration needs and deliberately
ships no workload code. This package is the SharePoint workload on top of it:
it knows *which* Graph calls to make, which fields to `$select`, how to
compose path-based addressing and upload URLs, and how to map the results.
Token acquisition, credential caching, the configured Graph client, error
translation, and paging all stay in `m365-client`.

## Delegated only, and why that matters here

Nothing here acquires a token. **Which identity the calls run as is the
caller's decision**, made by choosing which `GraphServiceClient` to pass in —
normally `M365Client.graph_for_user()`, so Graph returns only the sites and
files the signed-in user can already see, and every upload, move or deletion
lands under that user's name in version history and audit logs.

For SharePoint this is the whole design. App-only `Sites.*.All` is
tenant-wide and enterprises reject it; `Sites.Selected` scopes app-only
access to explicitly granted sites but needs a per-site grant step — an
operational process with an owner. Delegated access needs none of that, and
application code is never the only thing between a caller and every document
in the tenant. The package never references the app-only path or a
credential type. Tenant-wide background access belongs in a
separate, deliberately app-only component.

## Install

```bash
pip install -e ./sdks/python/m365/m365-client -e ./sdks/python/m365/sharepoint-client
```

See [Installation](../installation.md) for the Artifactory feed.

## Quickstart

```python
from m365_client import M365Client, M365Settings
from sharepoint_client import SharePointClient

m365 = M365Client(M365Settings(tenant_id=..., client_id=..., client_secret=...))

graph = await m365.graph_for_user(user.assertion, user.user_id)   # per request
sp = SharePointClient(graph)

sites = await sp.search_sites("intranet")            # search_sites() alone: recent sites
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

## API

All methods are `async` and raise the
[m365-client taxonomy](../m365-client/errors.md) on failure. Argument
problems (an empty id, a name containing `/`, an unknown `conflict` value, an
out-of-range `top`) raise `ValueError` before any network call.

### Sites and libraries

| Method | Returns | Notes |
|---|---|---|
| `search_sites(query=None, *, top=10)` | `list[Site]` | `None`/blank sends Graph's `*` wildcard — recently active sites. |
| `get_site(site_id)` | `Site` | `GraphNotFoundError` when missing *or invisible*; Graph's 404-before-403 is preserved. |
| `list_drives(site_id, *, top=10)` | `list[Drive]` | Document libraries with owner and quota. |

### Files and folders

| Method | Returns | Notes |
|---|---|---|
| `list_items(drive_id, *, path=None, item_id=None, top=10)` | `list[DriveItem]` | Children of the root, of a folder by `path`, or by `item_id`. Both together is a `ValueError`. |
| `get_item(drive_id, item_id)` | `DriveItem` | One file or folder. |
| `get_item_by_path(drive_id, path)` | `DriveItem` | Resolves `Reports/2026/Q3.md` to its item — and so its id — without walking the tree. |
| `download_item(drive_id, item_id)` | `bytes` | Fully buffered by the generated SDK — documents, not multi-gigabyte files. `GraphNotFoundError` for folders. |
| `search_files(drive_id, query, *, top=10)` | `list[DriveItem]` | Graph's drive search: file names and indexed content, across the whole library. |
| `upload_file(drive_id, *, name, content, parent_item_id=None, parent_path=None, conflict="replace")` | `DriveItem` | Simple PUT into the root, a folder by id, or a folder by path (not both). At most `MAX_UPLOAD_BYTES` (4 MB, Graph's single-request limit); bigger files need an upload session, out of scope here. |
| `create_folder(drive_id, name, *, parent_item_id=None, parent_path=None, conflict="fail")` | `DriveItem` | In the root, a folder by id, or a folder by path. |
| `move_item(drive_id, item_id, *, new_parent_id=None, new_name=None)` | `DriveItem` | Move, rename, or both within the drive; neither is a `ValueError`. `DriveItem.parent_id` from any listing is a ready-made destination. |
| `delete_item(drive_id, item_id)` | `None` | To the site recycle bin, recoverable by the user or a site admin. |

`conflict` decides what happens when the name is taken: `fail` (Graph
returns 409 → `GraphConflictError`), `replace` (overwrite — a new version in
libraries with versioning on), or `rename` (Graph appends a counter).
Uploads default to `replace`, folders to `fail`.

### Lists

| Method | Returns | Notes |
|---|---|---|
| `list_lists(site_id, *, top=10)` | `list[SharePointList]` | Every list the user can see — document libraries and hidden system lists included; filter on `template` and `hidden`. |
| `get_list_items(site_id, list_id, *, top=10)` | `list[ListItemRecord]` | Items with their `fields`: column internal names → values, `@odata` noise stripped. |

### `top` is a cap, not a page size

Graph pages are followed via `m365_client.collect()` until `top` items are
collected or the collection runs out. `top` must be between 1 and `MAX_TOP`
(50); an out-of-range value raises `ValueError` before any network call. A
consumer that needs a bigger walk streams with `m365_client.iter_pages`
against `SharePointClient.graph`, which is public for exactly that reason.

### Paths and names

Path-based addressing (`/drives/{id}/root:/{path}:`) has no generated
request builder, so the client composes the URL and hands it to `with_url`.
Segments are percent-encoded with `/` kept as the separator, so folder names
with spaces or `#` are safe to pass as-is. Names given to `upload_file`,
`create_folder` and `move_item` are single segments: a `/` in them is a
`ValueError`, not an implicit folder. Path-addressed writes resolve the
parent folder to its id first, so a misspelled `parent_path` fails with
`GraphNotFoundError` before anything is written.

## Models

Frozen dataclasses, no pydantic, matching `m365-client`. Every field except
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

Ids are opaque: site ids contain commas, drive item ids contain `!` and `.`.
`Identity.email` is read from Kiota's `additional_data`, because the generated
`Identity` model has no `email` field even though real responses carry one.
`ListItemRecord.fields` comes from the same place: list columns are
tenant-defined, so there is no typed model to map them into.

## Permissions

Graph checks the delegated permission per call, so grant only what the app
actually calls — an app registered with the read permissions alone gets a
`GraphAuthError` from `upload_file`, never a silent no-op.

| Operations | Delegated permissions |
|---|---|
| Sites, drives, files, search, lists | `Sites.Read.All`, `Files.Read.All` |
| Upload, create folder, move, rename, delete | `Sites.ReadWrite.All`, `Files.ReadWrite.All` |

## In a FastAPI service

In an app built from the [backend scaffold](https://pwc-me-adv-strategyand.github.io/infra-platform-services/scaffolds/backend/), `app/graph.py`
builds `M365Client` in the lifespan and validates the caller's access token;
its `get_graph` hands each handler a Graph client acting as the caller, and
the SharePoint client is one constructor call on top of it:

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

Let SDK errors propagate: the scaffold's `register_graph_error_handlers`
renders each typed error as the platform envelope.

## Against a real tenant

Nothing here needs a tenant to build or test against. For a first end-to-end
run you need an app registration that exposes an API scope, has the delegated
Graph permissions above admin-consented, and a caller that sends an **access**
token for that scope — the contract described in
[Authentication & token flows](../m365-client/authentication.md). A minimal
script that proves the whole chain is in the
[family README](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks/python/m365#trying-it-against-a-real-tenant).
