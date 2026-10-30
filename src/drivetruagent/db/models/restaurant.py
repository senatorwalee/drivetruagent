"""Who the restaurant is, as it registered."""

from sqlalchemy import CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import Base, Timestamps


class RestaurantProfile(Timestamps, Base):
    """What a restaurant said about itself when it came on board.

    At most one row: this deployment serves one restaurant. Until there is
    one, the name in the settings stands.
    """

    __tablename__ = "restaurant_profile"
    __table_args__ = (
        CheckConstraint("id = 1", name="one_row"),
        CheckConstraint("pickup_minutes > 0", name="pickup_minutes_positive"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=False, server_default=text("1")
    )
    name: Mapped[str]
    # Where phone orders are picked up. The agent says it when asked.
    address: Mapped[str | None]
    # How long a phone order takes to be ready, as the agent tells the caller.
    pickup_minutes: Mapped[int]
