import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

# JSONB is PostgreSQL-specific; SQLModel doesn't re-export it.
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import DateTime, Field, Index, Text, UniqueConstraint

from app.models.base import CreatedAtMixin, TimestampMixin, str_enum_type, utcnow

DEFAULT_CONVERSATION_TITLE = "New conversation"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # a tool or specialist result, kept for traceability
    SYSTEM = "system"  # notices such as "resumed an earlier conversation"


class Conversation(TimestampMixin, table=True):
    __tablename__ = "conversations"
    __table_args__ = (
        # "This user's most recent conversations" is the main lookup.
        Index("ix_conversations_user_id_last_activity_at", "user_id", "last_activity_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=200)
    status: ConversationStatus = Field(
        default=ConversationStatus.ACTIVE, sa_type=str_enum_type(ConversationStatus)
    )
    # Messages are numbered 1..message_count, so this is also the last message's number.
    message_count: int = Field(default=0)
    last_activity_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    # A short recap for quick recall. It never replaces the messages themselves.
    summary: str | None = Field(default=None, sa_type=Text)
    summary_through_sequence: int = Field(default=0)


class Message(CreatedAtMixin, table=True):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_id_sequence"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", ondelete="CASCADE")
    # 1, 2, 3... within the conversation: the source of chronological order
    # (timestamps can tie).
    sequence: int
    role: MessageRole = Field(sa_type=str_enum_type(MessageRole))
    content: str = Field(sa_type=Text)
    # Structured extras: spoken text, which agent answered, tool calls, display blocks.
    # Named `meta` because `metadata` is reserved on SQLModel/SQLAlchemy classes.
    meta: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
