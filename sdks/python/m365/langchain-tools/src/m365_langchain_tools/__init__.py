"""m365_langchain_tools -- Outlook and SharePoint as LangChain agent tools.

Twenty-four LLM-callable tools adapted from the M365 workload SDKs
(``outlook-client``, ``sharepoint-client``) for any LangChain-based agent
host: thirteen for mail, attachments, calendar and contacts; eleven for
sites, libraries, files and lists. Each set splits into reads and writes,
and a host can bind the read tools alone.

Host-agnostic on purpose: nothing here imports a specific platform, and the
one piece of trusted state -- who the calls act as -- enters through the
``graph_provider`` seam the host binds at construction time.

Typical wiring, once per agent execution::

    from m365_langchain_tools import outlook_tools, sharepoint_tools

    def graph_provider():
        # trusted runtime context: the app's M365Client and the signed-in
        # user's access token for the app's own API scope
        return m365.graph_for_user(assertion, user_oid)

    tools = [
        *outlook_tools(graph_provider),                      # reads + writes
        *sharepoint_tools(graph_provider, include_writes=False),
    ]
    llm_with_tools = llm.bind_tools(tools)

Delegated by intent: with a ``graph_for_user`` provider, Microsoft Graph
enforces the signed-in user's own permissions, so the tools can never read
anything the user could not, and every write -- a sent email, a created
event, an uploaded file -- goes out under that user's name. Identity is
never a model-visible argument.
"""

from langchain_core.tools import BaseTool

from m365_langchain_tools._common import GraphProvider
from m365_langchain_tools.outlook import (
    CreateOutlookDraftTool,
    CreateOutlookEventTool,
    ForwardOutlookMessageTool,
    GetOutlookMessageTool,
    ListOutlookContactsTool,
    ListOutlookEventsTool,
    ListOutlookFoldersTool,
    ListOutlookMessagesTool,
    MoveOutlookMessageTool,
    ReadOutlookAttachmentTool,
    ReplyOutlookMessageTool,
    RespondOutlookEventTool,
    SendOutlookMessageTool,
    outlook_tools,
)
from m365_langchain_tools.sharepoint import (
    CreateSharePointFolderTool,
    DeleteSharePointItemTool,
    GetSharePointListItemsTool,
    ListSharePointDrivesTool,
    ListSharePointFilesTool,
    ListSharePointListsTool,
    MoveSharePointItemTool,
    ReadSharePointFileTool,
    SearchSharePointFilesTool,
    SearchSharePointSitesTool,
    UploadSharePointFileTool,
    sharepoint_tools,
)

__all__ = [
    "CreateOutlookDraftTool",
    "CreateOutlookEventTool",
    "CreateSharePointFolderTool",
    "DeleteSharePointItemTool",
    "ForwardOutlookMessageTool",
    "GetOutlookMessageTool",
    "GetSharePointListItemsTool",
    "GraphProvider",
    "ListOutlookContactsTool",
    "ListOutlookEventsTool",
    "ListOutlookFoldersTool",
    "ListOutlookMessagesTool",
    "ListSharePointDrivesTool",
    "ListSharePointFilesTool",
    "ListSharePointListsTool",
    "MoveOutlookMessageTool",
    "MoveSharePointItemTool",
    "ReadOutlookAttachmentTool",
    "ReadSharePointFileTool",
    "ReplyOutlookMessageTool",
    "RespondOutlookEventTool",
    "SearchSharePointFilesTool",
    "SearchSharePointSitesTool",
    "SendOutlookMessageTool",
    "UploadSharePointFileTool",
    "m365_tools",
    "outlook_tools",
    "sharepoint_tools",
]


def m365_tools(graph_provider: GraphProvider, *, include_writes: bool = True) -> list[BaseTool]:
    """Every tool in this package, bound to one execution's Graph access."""
    return [
        *outlook_tools(graph_provider, include_writes=include_writes),
        *sharepoint_tools(graph_provider, include_writes=include_writes),
    ]
