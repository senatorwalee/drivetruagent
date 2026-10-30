"""Bringing a restaurant on, for the onboarding page."""

from fastapi import APIRouter, status

from drivetruagent.api.deps import BusinessDateDep, SessionDep, SpecialsCacheDep
from drivetruagent.api.errors import CONFLICT, REFUSED
from drivetruagent.ordering import onboarding
from drivetruagent.ordering.onboarding import Onboarding, OnboardingResult

router = APIRouter(prefix="/admin/onboarding", tags=["onboarding"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={**CONFLICT, **REFUSED},
    summary="Go live with everything the restaurant entered",
)
async def go_live(
    body: Onboarding, db: SessionDep, cache: SpecialsCacheDep, today: BusinessDateDep
) -> OnboardingResult:
    """The restaurant's details, its menu, its allergen, halal and kosher
    reviews and its combos, saved together: any refusal leaves everything as
    it was. With `replace_menu`, what is on the menu now comes off first. The
    agent sees it all from the next session."""
    result = await onboarding.go_live(db, body, today=today)
    await db.commit()
    # Today's specials are cached with their items; `replace_menu` may have
    # taken some off.
    cache.invalidate()
    return result
