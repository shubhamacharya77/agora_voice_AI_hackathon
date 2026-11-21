"""Short conversation recaps for quick recall, refreshed in the background after a turn.

A summary only helps find and brief a conversation. It never replaces the messages: exact
earlier answers are always read from the messages themselves.
"""

import logging
import uuid

from app.agents.llm import ChatModel
from app.conversations import store
from app.db import Database
from app.models import MessageRole

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = (
    "Summarize this conversation between a user and CoO, an operations assistant, in at most "
    "two short sentences for quick recall later: what the user wanted, what CoO did, and "
    "anything still open, such as a proposal waiting for approval. Plain text only."
)


async def summarize_if_due(
    db: Database, user_id: uuid.UUID, conversation_id: uuid.UUID, model: ChatModel, every: int
) -> None:
    """Refresh the recap once `every` new messages have arrived since the last one."""
    if every <= 0:
        return
    try:
        async with db.sessionmaker() as session:
            conversation = await store.get_conversation(session, user_id, conversation_id)
            if conversation is None:
                return
            through = conversation.message_count
            if through - conversation.summary_through_sequence < every:
                return
            messages = await store.recent_messages(session, user_id, conversation_id, limit=40)
            transcript = "\n".join(
                f"{m.role.value}: {m.content}"
                for m in messages
                if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
            )
            turn = await model.respond(
                system=SUMMARY_SYSTEM, messages=[{"role": "user", "content": transcript}], tools=[]
            )
            if turn.text:
                await store.set_summary(
                    session, user_id, conversation_id, turn.text, through_sequence=through
                )
                await session.commit()
    except Exception:  # a missing recap must never break a conversation
        logger.warning("couldn't refresh the summary for conversation_id=%s", conversation_id, exc_info=True)
