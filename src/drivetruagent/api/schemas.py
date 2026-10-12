"""Request bodies.

These check only the shape of a request: the right fields with the right
types. Whether the request is allowed (a quantity of zero, an unknown item)
is decided by the ordering rules, which answer with a typed error code.
"""

from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.enums import Language


class RequestModel(BaseModel):
    # A misspelt field is rejected, not silently ignored.
    model_config = ConfigDict(extra="forbid")


class OpenSessionRequest(RequestModel):
    lane: str = Field(default="lane-1", min_length=1, max_length=50)


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
