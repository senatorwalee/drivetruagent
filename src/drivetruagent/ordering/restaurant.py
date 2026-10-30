"""What a restaurant said about itself when it registered.

Everything here changes what the agent says. Until a restaurant has
registered there is no row, and the settings stand.

Nothing here commits. The caller owns the transaction.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import RestaurantProfile

MAX_PICKUP_MINUTES = 240

# One line: these go into the agent's instructions.
_ONE_LINE = r"^[^\r\n]*$"


class RestaurantDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=80, pattern=_ONE_LINE
        ),
    ]
    address: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, max_length=160, pattern=_ONE_LINE),
        ]
        | None
    ) = None
    pickup_minutes: int = Field(ge=1, le=MAX_PICKUP_MINUTES)

    @field_validator("address", mode="after")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return value or None


async def read_profile(db: AsyncSession) -> RestaurantProfile | None:
    return (
        await db.execute(
            select(RestaurantProfile).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def registered_name(db: AsyncSession, default: str) -> str:
    """The name the restaurant registered, or `default` if it hasn't."""
    profile = await read_profile(db)
    return profile.name if profile else default


async def save_profile(db: AsyncSession, details: RestaurantDetails) -> None:
    """Write the restaurant's details, replacing any it had."""
    values = details.model_dump()
    await db.execute(
        insert(RestaurantProfile)
        .values(id=1, **values)
        .on_conflict_do_update(
            index_elements=["id"], set_={**values, "updated_at": func.now()}
        )
    )
