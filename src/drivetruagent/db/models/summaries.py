"""The note written about each session after it ends, for staff to read.

The facts are code's, read from the event log, the order and the handoff.
The gist, follow-up and mood are the model's when its words passed the
checks in agent/summary.py, and code's own fixed wording otherwise.
"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.enums import Mood, SummaryAuthor


class SessionSummary(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "session_summaries"
    __table_args__ = (
        enum_check("written_by", SummaryAuthor),
        enum_check("mood", Mood),
        # Only the model judges mood, and only the model has a name and tokens.
        CheckConstraint(
            "(written_by = 'model') = (mood IS NOT NULL)",
            name="mood_only_from_the_model",
        ),
        CheckConstraint(
            "written_by = 'template' OR rejected_reason IS NULL",
            name="rejected_reason_only_on_template",
        ),
        # The list: newest first.
        Index("ix_session_summaries_created_at", "created_at"),
    )

    # Unique: a session has one summary. Writing it again replaces it.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    # agent.summary.SessionFacts, as JSON.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gist: Mapped[str]
    follow_up: Mapped[str | None]
    mood: Mapped[Mood | None] = mapped_column(text_enum(Mood))
    written_by: Mapped[SummaryAuthor] = mapped_column(text_enum(SummaryAuthor))
    # The model asked, whether or not its words were kept.
    model: Mapped[str | None]
    # Why the model's words were not used: none configured, it failed, or
    # which check they failed. None when there was nothing to ask about.
    rejected_reason: Mapped[str | None]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
