import uuid
from datetime import datetime
from enum import StrEnum

from sqlmodel import DateTime, Field, Text

from app.models.base import TimestampMixin, str_enum_type


class VoiceStatus(StrEnum):
    STARTING = "starting"  # asking Agora to start the agent
    ACTIVE = "active"  # the agent is in the channel; Agora calls our LLM endpoint
    ENDED = "ended"
    FAILED = "failed"  # Agora refused to start the agent


class VoiceSession(TimestampMixin, table=True):
    """One live voice call: the user and CoO's Agora agent in one RTC channel.

    Agora's cloud calls our custom-LLM endpoint with this session's id in the URL, which
    is how a call maps back to the logged-in user and their active conversation.
    """

    __tablename__ = "voice_sessions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    # The conversation this call talks in; it follows "resume" and "new conversation".
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", ondelete="CASCADE")
    channel: str = Field(max_length=64)
    user_uid: int
    agent_uid: int
    agent_id: str | None = Field(default=None, max_length=128)  # Agora's id for the agent
    status: VoiceStatus = Field(default=VoiceStatus.STARTING, sa_type=str_enum_type(VoiceStatus))
    error: str | None = Field(default=None, sa_type=Text)
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
