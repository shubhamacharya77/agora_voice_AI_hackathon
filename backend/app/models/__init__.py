"""Table models (SQLModel). Import every model module here so Alembic's autogenerate sees it."""

from app.models.base import CreatedAtMixin, SQLModel, TimestampMixin
from app.models.company import Employee, Ticket, TicketCategory, TicketStatus, TicketUrgency
from app.models.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from app.models.knowledge import KnowledgeChunk
from app.models.pending_action import OPEN_ACTION_STATUSES, ActionStatus, PendingAction
from app.models.user import Role, User, UserBase
from app.models.voice import VoiceSession, VoiceStatus

__all__ = [
    "DEFAULT_CONVERSATION_TITLE",
    "OPEN_ACTION_STATUSES",
    "ActionStatus",
    "Conversation",
    "ConversationStatus",
    "CreatedAtMixin",
    "Employee",
    "KnowledgeChunk",
    "Message",
    "MessageRole",
    "PendingAction",
    "Role",
    "SQLModel",
    "Ticket",
    "TicketCategory",
    "TicketStatus",
    "TicketUrgency",
    "TimestampMixin",
    "User",
    "UserBase",
    "VoiceSession",
    "VoiceStatus",
]
