# Microsoft 365 SDKs

One foundation package and the workload packages built on it. They are
grouped here because they ship as a family — same tenant setup, same
on-behalf-of token contract, same lifecycle and error taxonomy — while staying
separately installable and separately versioned.

```
m365/
├── m365-client/         foundation: Entra auth (on-behalf-of, app-only), credential
│                        caching, the configured Graph client, error taxonomy, paging,
│                        env mapping
├── outlook-client/      mail, attachments, calendar, contacts, profile  (depends on m365-client)
├── sharepoint-client/   sites, libraries, files, folders, lists         (depends on m365-client)
└── langchain-tools/     the workloads as LangChain agent tools    (depends on all three)
```

| Package | Import | Docs |
| --- | --- | --- |
| [`m365-client/`](m365-client/) | `m365_client` | [Getting started](../docs/m365-client/index.md) · [Authentication](../docs/m365-client/authentication.md) · [Errors](../docs/m365-client/errors.md) · [Corporate networks](../docs/m365-client/corporate-network.md) |
| [`outlook-client/`](outlook-client/) | `outlook_client` | [Getting started](../docs/outlook-client/index.md) |
| [`sharepoint-client/`](sharepoint-client/) | `sharepoint_client` | [Getting started](../docs/sharepoint-client/index.md) |
| [`langchain-tools/`](langchain-tools/) | `m365_langchain_tools` | [Getting started](../docs/m365-langchain-tools/index.md) |

## The split

`m365-client` owns what every Microsoft 365 integration needs and deliberately
ships **no workload code** — no `list_messages()`, no `get_site()`. The
workload packages own only what differs per workload: which Graph calls to
make, which fields to `$select`, and how to map results into typed models.
Each hands its calls the `GraphServiceClient` that `m365-client` produces, so
the identity a call runs as is always the caller's decision — normally
`M365Client.graph_for_user()`, which makes Graph enforce the signed-in user's
own permissions. A future Teams package follows the same shape.

`langchain-tools` sits one level higher again: the workload calls exposed as
LLM-callable LangChain tools for agent hosts, with identity entering through
a host-bound `graph_provider` seam rather than any model argument.

## Install

From a clone, in one `pip` invocation so the local editable `m365-client`
satisfies the workload packages' dependency on it:

```bash
pip install -e ./sdks/python/m365/m365-client \
  -e ./sdks/python/m365/outlook-client \
  -e ./sdks/python/m365/sharepoint-client \
  -e ./sdks/python/m365/langchain-tools
```

Published wheels come from the `m365-client-v*`, `outlook-client-v*`,
`sharepoint-client-v*` and `m365-langchain-tools-v*` release tags; release
`m365-client` first and `m365-langchain-tools` last when several change
together. Feed details:
[Python SDK installation](../docs/installation.md).

## Trying it against a real tenant

Nothing in this folder needs a tenant to build or test against. For a first
end-to-end run, three things outside the code have to be true (the reasons are
in the
[m365-client README](m365-client/README.md#the-input-contract-which-trips-everyone-once)):

1. The app registration **exposes an API scope** — `api://<client-id>/access_as_user`.
2. The Graph **delegated** permissions are added and **admin-consented**:
   `User.Read` plus, per workload, only what the app calls — reads such as
   `Mail.Read`, `Calendars.Read`, `Sites.Read.All`, `Files.Read.All`; writes
   such as `Mail.Send`, `Mail.ReadWrite`, `Calendars.ReadWrite`,
   `Sites.ReadWrite.All`, `Files.ReadWrite.All`. Each package README lists
   its permissions by operation.
3. The caller sends an **access token** for that scope — never an ID token.

With the Azure CLI added under the API's *Authorized client applications*
(its client id is `04b07795-8ddb-461a-bbee-02f9e1bf7b46`), a token for the
scope is one command, and the shortest proof of the whole chain is a few lines:

```bash
export USER_TOKEN=$(az account get-access-token --resource "api://<client-id>" --query accessToken -o tsv)
export USER_OID=$(az ad signed-in-user show --query id -o tsv)
```

```python
import asyncio, os
from m365_client import M365Client, M365Settings
from outlook_client import OutlookClient

async def main() -> None:
    m365 = M365Client(M365Settings(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    ))
    try:
        graph = await m365.graph_for_user(os.environ["USER_TOKEN"], os.environ["USER_OID"])
        print(await OutlookClient(graph).get_profile())
    finally:
        await m365.close()

asyncio.run(main())
```

If the printed `id` equals `USER_OID`, the on-behalf-of exchange is acting as
the signed-in user and not as the application. An `M365AuthError` carrying
`AADSTS65001` means the delegated permissions were never consented; a
`GraphAuthError` means a token was issued but the permission is missing.
