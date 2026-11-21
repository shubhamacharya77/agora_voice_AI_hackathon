"""Conversation history API (read-mostly).

Messages and pending actions are written by the agent (Phase 4 onward) through
`app.conversations.store`, never posted directly by clients: a client must not be able
to write an "assistant" message or invent an approval.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlmodel import SQLModel

from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.conversations import store
from app.deps import SessionDep, SettingsDep
from app.models import ActionStatus, Conversation, ConversationStatus, MessageRole, User

router = APIRouter(prefix="/conversations", tags=["conversations"])

ChatUser = Annotated[User, Depends(require_permission(Permission.CHAT))]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPublic(SQLModel):
    id: uuid.UUID
    title: str
    status: ConversationStatus
    message_count: int
    summary: str | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class MessagePublic(SQLModel):
    id: uuid.UUID
    sequence: int
    role: MessageRole
    content: str
    meta: dict[str, Any]
    created_at: datetime


class PendingActionPublic(SQLModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    action_type: str
    description: str
    proposal: dict[str, Any]
    version: int
    status: ActionStatus
    expires_at: datetime | None
    completed_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class SessionStart(BaseModel):
    conversation: ConversationPublic
    continued: bool  # True: picking up where the user just was; False: a fresh start


@router.post("/start", response_model=SessionStart)
async def start_session(user: ChatUser, session: SessionDep, settings: SettingsDep) -> SessionStart:
    """What the portal calls when it opens.

    A refresh or a reopen soon after continues the latest conversation. After a longer gap
    (CONVERSATION_CONTINUE_HOURS) a fresh one starts, and earlier conversations only come
    back when the user asks for them.
    """
    latest = (await store.list_conversations(session, user.id, limit=1)) or [None]
    conversation = latest[0]
    if conversation is not None:
        idle = datetime.now(UTC) - conversation.last_activity_at
        if conversation.message_count == 0:  # reuse an untouched one
            return SessionStart(conversation=ConversationPublic.model_validate(conversation), continued=False)
        if idle < timedelta(hours=settings.conversation_continue_hours):
            return SessionStart(conversation=ConversationPublic.model_validate(conversation), continued=True)
    fresh = await store.create_conversation(session, user.id)
    await session.commit()
    return SessionStart(conversation=ConversationPublic.model_validate(fresh), continued=False)


@router.post("", response_model=ConversationPublic, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    user: ChatUser, session: SessionDep, body: ConversationCreate | None = None
) -> Conversation:
    """Start a new, separate conversation ("New conversation")."""
    conversation = await store.create_conversation(
        session, user.id, title=body.title if body else None
    )
    await session.commit()
    return conversation


@router.get("", response_model=list[ConversationPublic])
async def list_conversations(
    user: ChatUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Conversation]:
    """Your conversations, most recently active first."""
    return list(await store.list_conversations(session, user.id, limit=limit, offset=offset))


@router.get("/{conversation_id}", response_model=ConversationPublic)
async def get_conversation(
    conversation_id: uuid.UUID, user: ChatUser, session: SessionDep
) -> Conversation:
    return await store.require_conversation(session, user.id, conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    conversation_id: uuid.UUID,
    user: ChatUser,
    session: SessionDep,
    after: Annotated[int, Query(ge=0, description="Return messages after this sequence")] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    """The original messages in order (summaries never replace them)."""
    return await store.list_messages(
        session, user.id, conversation_id, after_sequence=after, limit=limit
    )


@router.get("/{conversation_id}/pending-actions", response_model=list[PendingActionPublic])
async def list_pending_actions(
    conversation_id: uuid.UUID, user: ChatUser, session: SessionDep
):
    await store.require_conversation(session, user.id, conversation_id)
    return await store.list_pending_actions(session, user.id, conversation_id=conversation_id)
