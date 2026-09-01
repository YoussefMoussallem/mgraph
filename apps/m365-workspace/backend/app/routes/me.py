"""Identity route — proves the auth loop end to end.

``/v1/me`` is the backend counterpart of the frontend scaffold's
HomePage: the SPA acquires a token via MSAL, calls this with
``Authorization: Bearer``, and renders the claims. Replace/extend with
your app's real resources; the ``Annotated[CurrentUser,
Depends(get_current_user)]`` pattern is the part to keep.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/v1", tags=["identity"])


class MeResponse(BaseModel):
    """The signed-in caller, as seen by the backend."""

    id: str
    email: str
    display_name: str


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> MeResponse:
    return MeResponse(
        id=user.user_id,
        email=user.email,
        display_name=user.display_name,
    )
