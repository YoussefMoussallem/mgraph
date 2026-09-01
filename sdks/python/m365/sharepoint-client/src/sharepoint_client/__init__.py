"""sharepoint_client -- typed SharePoint access over Microsoft Graph.

Sites, document libraries (drives), folders, files (read, search, upload,
move, rename, delete) and lists, built on ``m365_client``, which owns
authentication, credential caching, the configured Graph client, error
translation, and paging. This package owns only what is specific to
SharePoint: which Graph calls to make, which fields to ``$select``, and how
to map the results into plain typed models.

Typical use in a FastAPI service::

    from app.graph import get_graph  # the backend scaffold's dependency
    from sharepoint_client import SharePointClient

    @router.get("/sites")
    async def list_sites(graph: Annotated[GraphServiceClient, Depends(get_graph)]):
        return await SharePointClient(graph).search_sites(top=10)

Delegated only by construction: the identity every call runs as is the
``GraphServiceClient`` the caller passes in, normally
``M365Client.graph_for_user()``. Graph then returns only what that user can
see, and every write lands under that user's name -- the property that
makes delegated SharePoint access acceptable where tenant-wide app-only
access is not.
"""

from sharepoint_client.client import (
    DEFAULT_TOP,
    MAX_TOP,
    MAX_UPLOAD_BYTES,
    SharePointClient,
)
from sharepoint_client.models import (
    Drive,
    DriveItem,
    Identity,
    ListItemRecord,
    SharePointList,
    Site,
)

__all__ = [
    "DEFAULT_TOP",
    "MAX_TOP",
    "MAX_UPLOAD_BYTES",
    "Drive",
    "DriveItem",
    "Identity",
    "ListItemRecord",
    "SharePointClient",
    "SharePointList",
    "Site",
]
