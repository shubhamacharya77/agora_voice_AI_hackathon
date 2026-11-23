"""One conversational turn: save what the user said, run the agent, save the outcome.

If a proposal is waiting for the user's answer, their message is first read as a reply to
it (app/agents/approvals.py). Approve, reject, "show it again" and unclear replies are
answered right there, without the agent. Changes and unrelated messages go to the agent,
which is told about the waiting proposal.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents import approvals
from app.agents.graph import TURN_GRAPH, AgentContext
from app.agents.llm import ChatModel
from app.agents.mock_data import now
from app.agents.prompts import build_system_prompt
from app.agents.tools import ToolContext, ToolRegistry, UserContext, proposal_card
from app.config import Settings
from app.conversations import store
from app.models import Message, MessageRole, PendingAction, User

logger = logging.getLogger(__name__)

ERROR_REPLY = "Sorry, I couldn't finish that. Please try again in a moment."
EMPTY_REPLY = "Sorry, I don't have an answer for that."
NEW_CONVERSATION_REPLY = "Sure, let's start a new conversation."
# Plain requests for a fresh start, handled without the model (variants go to the agent).
NEW_CONVERSATION_PHRASES = {
    "new conversation", "start a new conversation", "start new conversation", "new chat",
    "start over", "lets start over", "start fresh", "lets start fresh", "hey new conversation",
}  # fmt: skip


@dataclass
class TurnResult:
    user_message: Message
    assistant_message: Message
    reply: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)
    error: bool = False
    # The conversation the user continues in from now on (a resumed or a new one).
    switch_to: uuid.UUID | None = None


def model_history(history: list[Message]) -> list[dict[str, Any]]:
    """Earlier user/assistant messages as model turns: roles merged, starting with the user."""
    turns: list[dict[str, Any]] = []
    for message in history:
        if message.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue
        role = message.role.value
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] = merge_user_text(turns[-1]["content"], message.content)
        else:
            turns.append({"role": role, "content": message.content})
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)
    return turns


def merge_user_text(previous: str, new: str) -> str:
    """Join two things the user said in a row without repeating words.

    Speech recognition can send a growing sentence piece by piece ("Is there any" ->
    "Is there any maintenance issue?"), or the same words twice. A piece that extends the
    earlier one replaces it; a repeat is dropped; anything else is added after it.
    """
    old, fresh = previous.strip(), new.strip()
    if not old:
        return fresh
    if not fresh:
        return old
    flat_old, flat_new = " ".join(old.lower().split()), " ".join(fresh.lower().split())
    if flat_new.startswith(flat_old):
        return fresh
    if flat_new in flat_old:
        return old
    return f"{old}\n\n{fresh}"


def without_questions(text: str) -> str:
    """Drop sentences that end in a question mark; keep the rest as written."""
    # A sentence ends at . ! or ? followed by a space or the end, so "gmail.com" stays whole.
    sentences = re.findall(r"\S.*?(?:[.!?]+(?=\s|$)|$)", text.strip(), flags=re.DOTALL)
    return " ".join(s.strip() for s in sentences if s.strip() and not s.strip().endswith("?"))


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
    graph: Any = TURN_GRAPH,
    source: str = "text",  # "text" (typed or browser speech) or "voice" (Agora)
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
    waiting = await approvals.awaiting_decision(session, user_id, conversation_id)
    user_message = await store.append_message(
        session, user_id, conversation_id, MessageRole.USER, text, meta={"input": source}
    )
    await session.commit()  # the user's words are kept even if the agent fails
    started = time.perf_counter()

    # 0. "New conversation": a clean slate, no model needed. Anything waiting stays waiting
    #    in this conversation, ready to be picked up later.
    if approvals.normalize(text) in NEW_CONVERSATION_PHRASES:
        fresh = await store.create_conversation(session, user_id)
        return await _save_reply(
            session,
            user_id,
            conversation_id,
            user_message,
            reply=NEW_CONVERSATION_REPLY,
            cards=[],
            events=[],
            actions=[],
            model_name=model.name,
            model_calls=0,
            started=started,
            error=False,
            switch_to=fresh.id,
        )

    # 1. A proposal is waiting: is this the user's answer to it?
    verdict = None
    if waiting is not None:
        verdict = await approvals.decide(model, text, waiting)
        if verdict.decision in approvals.HANDLED_DIRECTLY:
            outcome = await approvals.handle(
                session,
                user_context,
                waiting,
                verdict,
                window_minutes=settings.approval_window_minutes,
            )
            return await _save_reply(
                session,
                user_id,
                conversation_id,
                user_message,
                reply=outcome.reply,
                cards=outcome.cards,
                events=[],
                actions=[outcome.action],
                model_name=model.name,
                model_calls=0 if verdict.source == "words" else 1,
                started=started,
                error=False,
                approval={
                    "action_id": str(outcome.action.id),
                    "decision": verdict.decision.value,
                    "decided_by": verdict.source,
                    "status": outcome.action.status.value,
                },
            )

    # 2. Otherwise (or for a change), run the agent.
    tool_context = ToolContext(user=user_context, conversation_id=conversation_id, session=session)
    notes: list[str] = []
    if waiting is not None and verdict is not None:
        if verdict.decision is approvals.Decision.CHANGE:
            tool_context.revising = waiting
            notes.append(approvals.change_note(waiting, verdict.change_request or text))
        else:
            notes.append(approvals.waiting_note(waiting))

    messages = model_history(history)
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = merge_user_text(messages[-1]["content"], text)
    else:
        messages.append({"role": "user", "content": text})
    context = AgentContext(
        model=model,
        registry=registry,
        tool_context=tool_context,
        system=build_system_prompt(
            user_context, now=now(), company=settings.company_name, summary=summary, notes=notes
        ),
        tools=registry.definitions_for(user_context),
        max_steps=settings.agent_max_steps,
    )

    error = False
    switch_to = None
    try:
        final = await graph.ainvoke(
            {"messages": messages, "tool_calls": [], "steps": 0, "reply": "", "cards": [], "events": []},
            # One LangGraph thread per conversation (used when checkpointing is on).
            config={
                "configurable": {"thread_id": str(conversation_id)},
                # Shown on the trace when LangSmith tracing is on.
                "run_name": "coo_turn",
                "metadata": {"conversation_id": str(conversation_id), "user": user_context.name,
                             "role": user_context.role.value},  # fmt: skip
            },
            context=context,
        )
        events, cards, steps = final["events"], final["cards"], final["steps"]
        # New or changed proposals, plus ones still waiting in a resumed conversation: each
        # is read back with its exact details, and its approval window starts now.
        proposals = list(tool_context.proposals)
        seen = {a.id for a in proposals}
        proposals += [a for a in tool_context.represent if a.id not in seen]
        for action in proposals:
            await approvals.mark_presented(session, action, settings.approval_window_minutes)
        if proposals:
            # CoO's read-back asks the approval question itself, so the model's own
            # questions ("Should I send it?") are dropped to avoid asking twice.
            reply = " ".join(
                part
                for part in [without_questions(final["reply"]), *map(approvals.confirmation, proposals)]
                if part
            )
            cards = [c for c in cards if c.get("type") != "proposal"] + [proposal_card(a) for a in proposals]
        else:
            reply = final["reply"].strip() or EMPTY_REPLY
        switch_to = tool_context.switch_to
        if switch_to is not None and tool_context.switch_reason == "resume":
            await store.append_message(
                session,
                user_id,
                switch_to,
                MessageRole.SYSTEM,
                "The user resumed this conversation.",
                meta={"resumed_from": str(conversation_id)},
            )
    except Exception:
        logger.exception("agent turn failed conversation_id=%s", conversation_id)
        # Drop anything half-done, such as a proposal the user would never hear about.
        await session.rollback()
        await session.refresh(user_message)  # committed earlier; reload what rollback expired
        reply, events, cards, steps, proposals, error = ERROR_REPLY, [], [], 0, [], True
        switch_to = None

    classifier_calls = 1 if verdict is not None and verdict.source != "words" else 0
    return await _save_reply(
        session,
        user_id,
        conversation_id,
        user_message,
        reply=reply,
        cards=cards,
        events=events,
        actions=proposals,
        model_name=model.name,
        model_calls=steps + classifier_calls,
        started=started,
        error=error,
        switch_to=switch_to,
    )


async def _save_reply(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message: Message,
    *,
    reply: str,
    cards: list[dict[str, Any]],
    events: list[dict[str, Any]],
    actions: list[PendingAction],
    model_name: str,
    model_calls: int,
    started: float,
    error: bool,
    approval: dict[str, Any] | None = None,
    switch_to: uuid.UUID | None = None,
) -> TurnResult:
    latency_ms = round((time.perf_counter() - started) * 1000)
    for event in events:
        await store.append_message(
            session, user_id, conversation_id, MessageRole.TOOL, _tool_line(event), meta=event
        )
    meta: dict[str, Any] = {
        "spoken_text": reply,
        "cards": cards,
        "specialists": sorted({e["specialist"] for e in events if e["specialist"]}),
        "action_ids": [str(a.id) for a in actions],
        "mock": any(e["mock"] for e in events),
        "model": model_name,
        "model_calls": model_calls,
        "latency_ms": latency_ms,
        "error": error,
    }
    if approval is not None:
        meta["approval"] = approval
    if switch_to is not None:
        meta["switch_to"] = str(switch_to)
    assistant_message = await store.append_message(
        session, user_id, conversation_id, MessageRole.ASSISTANT, reply, meta=meta
    )
    if switch_to is not None:
        # The conversation the user moves to becomes the latest, so reopening CoO later
        # continues there rather than in the one they just left.
        await store.touch_conversation(session, user_id, switch_to)
    await session.commit()
    logger.info(
        "turn done conversation_id=%s model_calls=%s tools=%s latency_ms=%s error=%s approval=%s",
        conversation_id,
        model_calls,
        len(events),
        latency_ms,
        error,
        approval["decision"] if approval else None,
    )
    return TurnResult(
        user_message=user_message,
        assistant_message=assistant_message,
        reply=reply,
        cards=cards,
        pending_actions=actions,
        error=error,
        switch_to=switch_to,
    )
