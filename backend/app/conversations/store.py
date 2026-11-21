"""Conversation storage, always scoped to one user.

Every function takes the caller's `user_id`. A conversation that belongs to someone else
behaves exactly as if it didn't exist, so neither the API nor the agent can read across
users by accident.

Functions flush but never commit: the caller owns the transaction.
"""

import re
import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlmodel import case, col, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    DEFAULT_CONVERSATION_TITLE,
    ActionStatus,
    Conversation,
    Message,
    MessageRole,
    PendingAction,
)
from app.search import ENGLISH, all_of, any_of, keywords

TITLE_MAX_LENGTH = 60


class ConversationNotFound(LookupError):
    """No such conversation for this user (it may exist but belong to someone else)."""


def title_from(content: str) -> str:
    """A conversation title made from the first thing the user said."""
    text = re.sub(r"\s+", " ", content).strip()
    if not text:
        return DEFAULT_CONVERSATION_TITLE
    if len(text) <= TITLE_MAX_LENGTH:
        return text
    return text[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


# --- conversations ---


async def create_conversation(
    session: AsyncSession, user_id: uuid.UUID, *, title: str | None = None
) -> Conversation:
    clean_title = (title or "").strip()[:200] or DEFAULT_CONVERSATION_TITLE
    conversation = Conversation(user_id=user_id, title=clean_title)
    session.add(conversation)
    await session.flush()
    return conversation


async def get_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    statement = (
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        # Always read the current row, even if an older copy is already in the session.
        .execution_options(populate_existing=True)
    )
    return (await session.exec(statement)).first()


async def require_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await get_conversation(session, user_id, conversation_id)
    if conversation is None:
        raise ConversationNotFound(conversation_id)
    return conversation


async def list_conversations(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> Sequence[Conversation]:
    """The user's conversations, most recently active first."""
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(col(Conversation.last_activity_at).desc(), col(Conversation.id).desc())
        .offset(offset)
        .limit(limit)
    )
    return (await session.exec(statement)).all()


async def touch_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    """Make this the user's most recently active conversation (e.g. after switching to it).

    clock_timestamp(), not now(): now() is the transaction's start time, which would tie
    with the reply just saved in the conversation the user switched away from.
    """
    statement = (
        update(Conversation)
        .where(col(Conversation.id) == conversation_id, col(Conversation.user_id) == user_id)
        .values(last_activity_at=func.clock_timestamp())
        .execution_options(synchronize_session=False)
    )
    await session.exec(statement)


async def search_conversations(
    session: AsyncSession,
    user_id: uuid.UUID,
    topic: str,
    *,
    exclude: uuid.UUID | None = None,
    limit: int = 3,
) -> list[Conversation]:
    """This user's conversations about `topic`, best match first.

    Conversations whose messages contain every keyword win; if none do, any keyword
    counts. Only user and assistant messages are searched.
    """
    words = keywords(topic)
    if not words:
        return []
    for terms in (all_of(words), any_of(words)):
        vector = func.to_tsvector(ENGLISH, col(Message.content))
        tsquery = func.to_tsquery(ENGLISH, terms)
        best = func.max(func.ts_rank_cd(vector, tsquery)).label("rank")
        statement = (
            select(col(Message.conversation_id), best)
            .join(Conversation, col(Conversation.id) == col(Message.conversation_id))
            .where(
                Conversation.user_id == user_id,
                col(Message.role).in_([MessageRole.USER, MessageRole.ASSISTANT]),
                vector.bool_op("@@")(tsquery),
            )
        )
        if exclude is not None:
            statement = statement.where(col(Message.conversation_id) != exclude)
        statement = statement.group_by(col(Message.conversation_id)).order_by(best.desc()).limit(limit)
        ids = [row[0] for row in (await session.exec(statement)).all()]
        if ids:
            found = {
                c.id: c
                for c in (await session.exec(select(Conversation).where(col(Conversation.id).in_(ids)))).all()
            }
            return [found[i] for i in ids if i in found]
    return []


async def set_summary(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    summary: str,
    *,
    through_sequence: int,
) -> None:
    """Store the recap of messages 1..through_sequence. The messages stay untouched."""
    statement = (
        update(Conversation)
        .where(col(Conversation.id) == conversation_id, col(Conversation.user_id) == user_id)
        .values(summary=summary, summary_through_sequence=through_sequence)
        .execution_options(synchronize_session=False)
    )
    result = await session.exec(statement)
    if result.rowcount == 0:
        raise ConversationNotFound(conversation_id)


# --- messages ---


async def append_message(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: MessageRole,
    content: str,
    *,
    meta: dict[str, Any] | None = None,
) -> Message:
    """Add a message at the end of a conversation and return it.

    One UPDATE ... RETURNING on the conversation hands out the next sequence number and
    locks that row until commit, so concurrent appends can never share a number. The
    first user message also becomes the title if the conversation still has the default.
    """
    values: dict[str, Any] = {
        "message_count": Conversation.message_count + 1,
        "last_activity_at": func.now(),
    }
    if role is MessageRole.USER:
        values["title"] = case(
            (Conversation.title == DEFAULT_CONVERSATION_TITLE, title_from(content)),
            else_=Conversation.title,
        )
    statement = (
        update(Conversation)
        .where(col(Conversation.id) == conversation_id, col(Conversation.user_id) == user_id)
        .values(**values)
        .returning(col(Conversation.message_count))
        .execution_options(synchronize_session=False)
    )
    sequence = (await session.exec(statement)).scalar_one_or_none()
    if sequence is None:
        raise ConversationNotFound(conversation_id)

    message = Message(
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        content=content,
        meta=meta or {},
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    after_sequence: int = 0,
    limit: int = 200,
) -> Sequence[Message]:
    """Messages in chronological order, starting after `after_sequence`."""
    await require_conversation(session, user_id, conversation_id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.sequence > after_sequence)
        .order_by(col(Message.sequence))
        .limit(limit)
    )
    return (await session.exec(statement)).all()


async def recent_messages(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, *, limit: int = 20
) -> list[Message]:
    """The last `limit` messages, oldest first (the agent's working context)."""
    await require_conversation(session, user_id, conversation_id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(col(Message.sequence).desc())
        .limit(limit)
    )
    newest_first = (await session.exec(statement)).all()
    return list(reversed(newest_first))


# --- pending actions ---


async def create_pending_action(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    action_type: str,
    description: str,
    proposal: dict[str, Any],
) -> PendingAction:
    """Record a proposed action (status `requested`, version 1). Nothing runs yet."""
    await require_conversation(session, user_id, conversation_id)
    action = PendingAction(
        conversation_id=conversation_id,
        user_id=user_id,
        action_type=action_type,
        description=description,
        proposal=proposal,
    )
    session.add(action)
    await session.flush()
    return action


async def list_pending_actions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    conversation_id: uuid.UUID | None = None,
    statuses: Collection[ActionStatus] | None = None,
) -> Sequence[PendingAction]:
    """The user's actions, newest first, optionally for one conversation or some statuses."""
    statement = select(PendingAction).where(PendingAction.user_id == user_id)
    if conversation_id is not None:
        statement = statement.where(PendingAction.conversation_id == conversation_id)
    if statuses:
        statement = statement.where(col(PendingAction.status).in_(list(statuses)))
    statement = statement.order_by(col(PendingAction.created_at).desc())
    return (await session.exec(statement)).all()
