"""One conversational turn: save what the user said, run the agent, save the outcome."""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents.graph import TURN_GRAPH, AgentContext
from app.agents.llm import ChatModel
from app.agents.mock_data import now
from app.agents.prompts import build_system_prompt
from app.agents.tools import ToolContext, ToolRegistry, UserContext
from app.config import Settings
from app.conversations import store
from app.models import Message, MessageRole, PendingAction, User

logger = logging.getLogger(__name__)

ERROR_REPLY = "Sorry, I couldn't finish that. Please try again in a moment."
EMPTY_REPLY = "Sorry, I don't have an answer for that."


@dataclass
class TurnResult:
    user_message: Message
    assistant_message: Message
    reply: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)
    error: bool = False


def model_history(history: list[Message]) -> list[dict[str, Any]]:
    """Earlier user/assistant messages as model turns: roles merged, starting with the user."""
    turns: list[dict[str, Any]] = []
    for message in history:
        if message.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue
        role = message.role.value
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + message.content
        else:
            turns.append({"role": role, "content": message.content})
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)
    return turns


def _tool_line(event: dict[str, Any]) -> str:
    outcome = "ok" if event["ok"] else f"failed: {event['output'].get('error', 'error')}"
    return f"{event['tool']} ({event['specialist'] or 'unknown'}): {outcome}"


async def run_turn(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    text: str,
    *,
    model: ChatModel,
    registry: ToolRegistry,
    settings: Settings,
) -> TurnResult:
    # Plain values from here on: a rollback below expires every ORM object in the session,
    # and touching an expired object would trigger a lazy load the async driver forbids.
    user_id = user.id
    user_context = UserContext.from_user(user)

    conversation = await store.require_conversation(session, user_id, conversation_id)
    history = await store.recent_messages(
        session, user_id, conversation_id, limit=settings.agent_history_messages
    )
    summary = conversation.summary
    user_message = await store.append_message(
        session, user_id, conversation_id, MessageRole.USER, text, meta={"input": "text"}
    )
    await session.commit()  # the user's words are kept even if the agent fails

    tool_context = ToolContext(user=user_context, conversation_id=conversation_id, session=session)
    messages = model_history(history)
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + text
    else:
        messages.append({"role": "user", "content": text})
    context = AgentContext(
        model=model,
        registry=registry,
        tool_context=tool_context,
        system=build_system_prompt(
            user_context, now=now(), company=settings.company_name, summary=summary
        ),
        tools=registry.definitions_for(user_context),
        max_steps=settings.agent_max_steps,
    )

    started = time.perf_counter()
    error = False
    try:
        final = await TURN_GRAPH.ainvoke(
            {"messages": messages, "tool_calls": [], "steps": 0, "reply": "", "cards": [], "events": []},
            context=context,
        )
        reply = final["reply"].strip() or EMPTY_REPLY
        events, cards, steps = final["events"], final["cards"], final["steps"]
        proposals = list(tool_context.proposals)
    except Exception:
        logger.exception("agent turn failed conversation_id=%s", conversation_id)
        # Drop anything half-done, such as a proposal the user would never hear about.
        await session.rollback()
        await session.refresh(user_message)  # committed earlier; reload what rollback expired
        reply, events, cards, steps, proposals, error = ERROR_REPLY, [], [], 0, [], True
    latency_ms = round((time.perf_counter() - started) * 1000)

    for event in events:
        await store.append_message(
            session, user_id, conversation_id, MessageRole.TOOL, _tool_line(event), meta=event
        )
    assistant_message = await store.append_message(
        session,
        user_id,
        conversation_id,
        MessageRole.ASSISTANT,
        reply,
        meta={
            "spoken_text": reply,
            "cards": cards,
            "specialists": sorted({e["specialist"] for e in events if e["specialist"]}),
            "pending_action_ids": [str(a.id) for a in proposals],
            "mock": any(e["mock"] for e in events),
            "model": model.name,
            "model_calls": steps,
            "latency_ms": latency_ms,
            "error": error,
        },
    )
    await session.commit()
    logger.info(
        "turn done conversation_id=%s model_calls=%s tools=%s latency_ms=%s error=%s",
        conversation_id,
        steps,
        len(events),
        latency_ms,
        error,
    )
    return TurnResult(
        user_message=user_message,
        assistant_message=assistant_message,
        reply=reply,
        cards=cards,
        pending_actions=proposals,
        error=error,
    )
