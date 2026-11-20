"""Consequential actions that wait for the user's explicit approval.

The approval flow itself (Phase 5) moves a row through these states. The table is built
so the safety rules can be enforced in the database: an approval names a version, and
an idempotency key stops the same action running twice.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

# JSONB is PostgreSQL-specific; SQLModel doesn't re-export it.
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import DateTime, Field, Index, Text

from app.models.base import TimestampMixin, str_enum_type


class ActionStatus(StrEnum):
    REQUESTED = "requested"  # proposal prepared, not yet shown to the user
    PENDING = "pending"  # shown and read out; waiting for an explicit yes or no
    APPROVED = "approved"  # the user approved this exact version
    EXECUTING = "executing"  # the external call is in flight
    SUCCEEDED = "succeeded"  # the provider confirmed success
    FAILED = "failed"  # the provider reported a failure
    UNCERTAIN = "uncertain"  # interrupted mid-call; the outcome is unknown until checked
    CANCELLED = "cancelled"  # the user said no
    EXPIRED = "expired"  # no answer within the approval window


# Still waiting on the user or about to run: what "where did we stop?" looks for.
OPEN_ACTION_STATUSES = frozenset(
    {ActionStatus.REQUESTED, ActionStatus.PENDING, ActionStatus.APPROVED}
)


class PendingAction(TimestampMixin, table=True):
    __tablename__ = "pending_actions"
    __table_args__ = (Index("ix_pending_actions_user_id_status", "user_id", "status"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversations.id", ondelete="CASCADE", index=True
    )
    # Who asked for it. Only this user can approve it.
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    action_type: str = Field(max_length=64)  # e.g. "email.send", "meeting.cancel"
    description: str = Field(max_length=500)  # one line for listings and recaps
    # The exact action that runs if approved.
    proposal: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    # Goes up by one on every change; an approval must name the version it approves.
    version: int = Field(default=1)
    status: ActionStatus = Field(
        default=ActionStatus.REQUESTED, sa_type=str_enum_type(ActionStatus)
    )
    # Sent to the provider where supported, and checked before every execution.
    idempotency_key: uuid.UUID = Field(default_factory=uuid.uuid4, unique=True)
    presented_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    approved_version: int | None = None
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # What the provider actually returned (none_as_null: Python None is SQL NULL).
    result: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    error: str | None = Field(default=None, sa_type=Text)
