"""The signed-in user's saved financial profile and portfolio.

Requires an account, like run history, and for the same reason: there is nowhere to save an
anonymous user's data and nothing to look it up by. What it is *not* is a gate on analysis — an
anonymous caller still gets the full deterministic half by posting their profile with each
request. Signing in only means they stop having to retype it.

No Anthropic key touches this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import SavedProfileResponse, SaveProfileRequest
from app.core.supabase_auth import AuthUser, current_user_required
from app.db.pool import StorageUnavailable
from app.db.repositories import profiles as profiles_repo

router = APIRouter(prefix="/profile", tags=["profile"])


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "storage_unavailable",
            "message": "Saving your profile is not available on this deployment right now.",
        },
    )


@router.get("", response_model=SavedProfileResponse)
async def get_my_profile(user: AuthUser = Depends(current_user_required)) -> SavedProfileResponse:
    try:
        saved = await profiles_repo.load(user.id)
    except StorageUnavailable as exc:
        raise _storage_unavailable() from exc

    if saved is None:
        return SavedProfileResponse(profile=None, portfolio=None)
    profile, portfolio = saved
    return SavedProfileResponse(profile=profile, portfolio=portfolio)


@router.put("", response_model=SavedProfileResponse)
async def save_my_profile(
    req: SaveProfileRequest, user: AuthUser = Depends(current_user_required)
) -> SavedProfileResponse:
    try:
        await profiles_repo.save(user.id, req.profile, req.portfolio)
    except StorageUnavailable as exc:
        raise _storage_unavailable() from exc

    # Echo back what was stored rather than what was sent: `notes` may have been scrubbed on the
    # way in, and the client should be showing the stored text, not the text it tried to store.
    saved = await profiles_repo.load(user.id)
    if saved is None:  # pragma: no cover - only reachable if the row vanished mid-request
        return SavedProfileResponse(profile=None, portfolio=None)
    profile, portfolio = saved
    return SavedProfileResponse(profile=profile, portfolio=portfolio)
