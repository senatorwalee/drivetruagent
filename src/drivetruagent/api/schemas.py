"""Request bodies.

These check only the shape of a request: the right fields with the right
types. Whether the request is allowed (a quantity of zero, an unknown item)
is decided by the ordering rules, which answer with a typed error code.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from drivetruagent.enums import Channel, Language


class RequestModel(BaseModel):
    # A misspelt field is rejected, not silently ignored.
    model_config = ConfigDict(extra="forbid")


class OpenSessionRequest(RequestModel):
    # Left out: "lane-1" for the drive-through, a lane of its own for a call.
    lane: str | None = Field(default=None, min_length=1, max_length=50)
    channel: Channel = Channel.DRIVE_THRU
    # For testing only, and refused unless ALLOW_TYPED_CALLER_ID is on: a real
    # caller's number comes from the phone line, which can't be typed over.
    caller_phone: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _caller_only_on_phone(self) -> Self:
        if self.caller_phone is not None and self.channel is not Channel.PHONE:
            raise ValueError("caller_phone is only for channel 'phone'")
        return self


class UpdateSessionRequest(RequestModel):
    language: Language


class AddLineRequest(RequestModel):
    item_code: str = Field(min_length=1)
    quantity: int = 1
    # Group code -> option codes, e.g. {"size": ["large"], "dip": ["ketchup"]}.
    selections: dict[str, list[str]] = Field(default_factory=dict)


class UpdateLineRequest(RequestModel):
    quantity: int | None = None
    # Only the groups named here change; an empty list clears an optional group.
    selections: dict[str, list[str]] | None = None
