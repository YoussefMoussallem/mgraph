"""outlook_client -- typed Outlook access over Microsoft Graph.

Mail (read, search, send, reply, forward, drafts, move, delete, attachments),
calendar (list, create, update, respond, delete) and contacts, built on
``m365_client``, which owns authentication, credential caching, the
configured Graph client, error translation, and paging. This package owns
only what is specific to Outlook: which Graph calls to make, which fields
to ``$select``, and how to map the results into plain typed models.

Typical use in a FastAPI service::

    from app.graph import get_graph  # the backend scaffold's dependency
    from outlook_client import OutlookClient

    @router.get("/messages")
    async def list_messages(graph: Annotated[GraphServiceClient, Depends(get_graph)]):
        messages = await OutlookClient(graph).list_messages(top=10)
        ...

Delegated only by construction: the identity every call runs as is the
``GraphServiceClient`` the caller passes in, normally
``M365Client.graph_for_user()``. Writes go out as that user -- a sent mail
comes from their mailbox, a created event lands on their calendar.
"""

from outlook_client.client import (
    DEFAULT_TOP,
    MAX_ATTACHMENT_BYTES,
    MAX_TOP,
    OutlookClient,
)
from outlook_client.models import (
    Attachment,
    AttachmentContent,
    Attendee,
    Contact,
    Event,
    MailFolder,
    MessageDetail,
    MessageSummary,
    Recipient,
    UserProfile,
)

__all__ = [
    "DEFAULT_TOP",
    "MAX_ATTACHMENT_BYTES",
    "MAX_TOP",
    "Attachment",
    "AttachmentContent",
    "Attendee",
    "Contact",
    "Event",
    "MailFolder",
    "MessageDetail",
    "MessageSummary",
    "OutlookClient",
    "Recipient",
    "UserProfile",
]
