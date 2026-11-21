"""Recall and resume: finding earlier conversations, reading them, picking one up again.

New conversations start clean. These tools are the only way earlier conversations come
back, and only when the user asks. Everything is scoped to the logged-in user.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents.tools import Specialist, ToolContext, ToolError, ToolOutput, ToolSpec
from app.auth.permissions import Permission
from app.conversations import store
from app.models import ActionStatus, Conversation, MessageRole

WAITING = [ActionStatus.REQUESTED, ActionStatus.PENDING]


class FindInput(BaseModel):
    topic: str | None = Field(
        default=None,
        description="What it was about, e.g. 'deployment delay'. Leave empty for the most recent ones.",
    )


class ReadInput(BaseModel):
    conversation_id: uuid.UUID
    last: int = Field(default=6, ge=1, le=20, description="How many of its latest messages to read")


class ConversationInput(BaseModel):
    conversation_id: uuid.UUID


class NoInput(BaseModel):
    pass


async def recap(session: AsyncSession, user_id: uuid.UUID, conversation: Conversation) -> dict[str, Any]:
    """Where a conversation stands: its summary, its last exchange and anything still waiting."""
    recent = await store.recent_messages(session, user_id, conversation.id, limit=12)
    last_user = next((m.content for m in reversed(recent) if m.role is MessageRole.USER), None)
    last_reply = next((m.content for m in reversed(recent) if m.role is MessageRole.ASSISTANT), None)
    waiting = await store.list_pending_actions(
        session, user_id, conversation_id=conversation.id, statuses=WAITING
    )
    return {
        "conversation_id": str(conversation.id),
        "title": conversation.title,
        "last_active": conversation.last_activity_at.isoformat(timespec="minutes"),
        "messages": conversation.message_count,
        "summary": conversation.summary,
        "last_user_message": last_user,
        "last_assistant_message": last_reply,
        "waiting_for_approval": [a.description for a in waiting],
    }


async def find_conversations(ctx: ToolContext, args: FindInput) -> ToolOutput:
    if args.topic:
        matches = await store.search_conversations(
            ctx.session, ctx.user.id, args.topic, exclude=ctx.conversation_id
        )
    else:
        recent = await store.list_conversations(ctx.session, ctx.user.id, limit=5)
        matches = [c for c in recent if c.id != ctx.conversation_id and c.message_count > 0][:3]
    recaps = [await recap(ctx.session, ctx.user.id, c) for c in matches]
    return ToolOutput(
        data={"conversations": recaps, "found": len(recaps)},
        card={"type": "conversations", "items": recaps} if recaps else None,
    )


async def read_conversation(ctx: ToolContext, args: ReadInput) -> ToolOutput:
    try:
        recent = await store.recent_messages(
            ctx.session, ctx.user.id, args.conversation_id, limit=args.last * 3
        )
    except store.ConversationNotFound:
        raise ToolError("There's no such conversation.") from None
    said = [m for m in recent if m.role in (MessageRole.USER, MessageRole.ASSISTANT)][-args.last :]
    messages = [
        {
            "sequence": m.sequence,
            "role": m.role.value,
            "text": m.content,  # the original words, never a summary
            "at": m.created_at.isoformat(timespec="minutes"),
        }
        for m in said
    ]
    return ToolOutput(data={"conversation_id": str(args.conversation_id), "messages": messages})


async def resume_conversation(ctx: ToolContext, args: ConversationInput) -> ToolOutput:
    if args.conversation_id == ctx.conversation_id:
        raise ToolError("That's the conversation you're already in.")
    conversation = await store.get_conversation(ctx.session, ctx.user.id, args.conversation_id)
    if conversation is None:
        raise ToolError("There's no such conversation.")
    waiting = await store.list_pending_actions(
        ctx.session, ctx.user.id, conversation_id=conversation.id, statuses=WAITING
    )
    ctx.switch_to, ctx.switch_reason = conversation.id, "resume"
    ctx.represent.extend(waiting)
    return ToolOutput(data={"resumed": True, **await recap(ctx.session, ctx.user.id, conversation)})


async def start_new_conversation(ctx: ToolContext, args: NoInput) -> ToolOutput:
    conversation = await store.create_conversation(ctx.session, ctx.user.id)
    ctx.switch_to, ctx.switch_reason = conversation.id, "new"
    return ToolOutput(data={"started": True, "conversation_id": str(conversation.id)})


CONVERSATIONS = Specialist(
    name="recall",
    description="The user's earlier conversations: find, read, resume, or start a new one.",
    tools=(
        ToolSpec(
            name="conversations_find",
            description="Find the user's earlier conversations, by topic or most recent first. "
            "Returns each one's title, summary, last exchange and anything still waiting.",
            input_model=FindInput,
            permission=Permission.CHAT,
            handler=find_conversations,
            mock=False,
        ),
        ToolSpec(
            name="conversations_read",
            description="Read the exact latest messages of an earlier conversation, e.g. to "
            "repeat a previous answer word for word.",
            input_model=ReadInput,
            permission=Permission.CHAT,
            handler=read_conversation,
            mock=False,
        ),
        ToolSpec(
            name="conversations_resume",
            description="Continue an earlier conversation: it becomes the active one again.",
            input_model=ConversationInput,
            permission=Permission.CHAT,
            handler=resume_conversation,
            mock=False,
        ),
        ToolSpec(
            name="conversations_start_new",
            description="Start a new, separate conversation with a clean slate.",
            input_model=NoInput,
            permission=Permission.CHAT,
            handler=start_new_conversation,
            mock=False,
        ),
    ),
)
