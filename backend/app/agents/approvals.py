"""Human-in-the-loop approval: the only path from a proposal to a real action.

When a proposal is waiting in the conversation, the user's next message is read first:

    decide()  ->  approve | reject | change | show | unrelated | unclear
    approve   ->  approve()   one atomic UPDATE, bound to the version that was read out
                              and to the approval window
              ->  execute()   claims the action exactly once, runs it, checks the
                              provider's answer, records what really happened

Safety rules enforced here:
- A "yes" only counts for a proposal that was presented and is still inside its window.
- Mixed or hesitant replies are "unclear", and CoO asks again.
- The model's opinion alone never approves: the user's own words must contain a clear
  yes and nothing that sounds like no, "but" or "change".
- Each action runs at most once, across retries and restarts.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlmodel import col, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents import executors
from app.agents.llm import ChatModel, ModelError
from app.agents.tools import UserContext, proposal_card
from app.models import ActionStatus, PendingAction
from app.models.base import utcnow

logger = logging.getLogger(__name__)


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CHANGE = "change"
    SHOW = "show"
    UNRELATED = "unrelated"
    UNCLEAR = "unclear"


# Decisions answered here without running the agent.
HANDLED_DIRECTLY = frozenset({Decision.APPROVE, Decision.REJECT, Decision.SHOW, Decision.UNCLEAR})


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    change_request: str | None = None
    source: str = "words"  # "words", "model", "model+guard" or "fallback"


# --- reading the user's reply ---

_DROP = {"please", "thanks", "thank", "you", "coo"}
_APPROVE = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "yes yes", "go ahead", "yes go ahead",
    "go for it", "approve", "approved", "i approve", "approve it", "confirm", "confirmed",
    "yes confirm", "do it", "yes do it", "proceed", "yes proceed", "send it", "yes send it",
    "yes send", "send it now", "ok send it", "okay send it", "looks good send it",
    "submit it", "yes submit it", "thats fine", "yes thats fine", "correct", "yes correct",
}  # fmt: skip
_REJECT = {
    "no", "nope", "no dont", "dont", "cancel", "cancel it", "no cancel", "no cancel it",
    "dont send it", "do not send it", "dont do it", "do not do it", "dont submit it", "stop",
    "no dont send it", "no do not send it", "no dont do it", "no dont submit it", "no stop",
    "never mind", "nevermind", "no never mind", "forget it", "no forget it", "scrap it",
    "discard it",
}  # fmt: skip
_SHOW = {
    "show me", "show it", "show it again", "show me again", "show me the draft",
    "show me the draft again", "show the draft", "read it", "read it again", "read it back",
    "read it to me", "repeat", "repeat it", "repeat that", "say that again", "what was it",
}  # fmt: skip
_CHANGE = {"change", "change it", "edit it", "modify it", "i want to change it", "make changes"}
# "Cancel it" can't be read either way when the proposal is itself a cancellation.
_AMBIGUOUS_FOR_CANCELLATIONS = {"cancel", "cancel it", "no cancel", "no cancel it"}

_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|approve|approved|confirm|confirmed|go ahead|go for it|"
    r"proceed|send|submit|do it|looks good|sounds good|fine|correct|perfect)\b"
)
_DOUBT = re.compile(
    r"\b(no|not|nope|dont|never|cancel|stop|wait|hold|change|instead|but|except|later|"
    r"tomorrow|tonight|unless|actually|edit|modify|fix|wrong|maybe|hmm|if)\b"
)


def normalize(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))
    return " ".join(w for w in words if w not in _DROP)


def decide_from_words(text: str, action_type: str) -> Verdict | None:
    """Exact, common replies decided without the model. None = not one of them."""
    reply = normalize(text)
    if action_type == "meeting.cancel" and reply in _AMBIGUOUS_FOR_CANCELLATIONS:
        return None
    for phrases, decision in (
        (_APPROVE, Decision.APPROVE),
        (_REJECT, Decision.REJECT),
        (_SHOW, Decision.SHOW),
        (_CHANGE, Decision.CHANGE),
    ):
        if reply in phrases:
            return Verdict(decision)
    return None


def words_allow_approval(text: str, action_type: str) -> bool:
    """A clear yes, and nothing that sounds like no, hesitation or a change."""
    reply = normalize(text)
    if action_type == "meeting.cancel":  # "yes, cancel the meeting" is a yes here
        reply = re.sub(r"\bcancel\b", "", reply)
    return bool(_AFFIRM.search(reply)) and not _DOUBT.search(reply)


CLASSIFIER_SYSTEM = """You classify one reply from a user to a proposal an assistant is waiting to carry out.

Proposal: {description}

Pick exactly one decision and call record_decision:
- approve: clearly says to go ahead with exactly this proposal, unchanged.
- reject: says no, cancel, or don't do it.
- change: wants something in the proposal changed. Put the requested change in change_request.
- show: wants to see or hear the proposal again.
- unrelated: is about something else.
- unclear: anything else, including hesitation, questions or mixed signals.

When in doubt, choose unclear. Never choose approve if the reply also asks for a change."""

DECISION_TOOL: dict[str, Any] = {
    "name": "record_decision",
    "description": "Record what the user's reply to the proposal means.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": [d.value for d in Decision]},
            "change_request": {"type": "string", "description": "Only for decision=change"},
        },
        "required": ["decision"],
    },
}


async def decide(model: ChatModel, text: str, action: PendingAction) -> Verdict:
    verdict = decide_from_words(text, action.action_type)
    if verdict is not None:
        return verdict
    try:
        # The classifier sees only the proposal and the user's words: no emails or
        # web pages that could carry injected instructions.
        turn = await model.respond(
            system=CLASSIFIER_SYSTEM.format(description=action.description),
            messages=[{"role": "user", "content": text}],
            tools=[DECISION_TOOL],
        )
    except ModelError:
        logger.warning("approval classifier unavailable; asking the user again")
        return Verdict(Decision.UNCLEAR, source="fallback")
    except Exception:  # any other failure also means "ask again", never "approve"
        logger.exception("approval classifier failed; asking the user again")
        return Verdict(Decision.UNCLEAR, source="fallback")
    call = next((c for c in turn.tool_calls if c.name == "record_decision"), None)
    try:
        decision = Decision(call.input.get("decision")) if call else Decision.UNCLEAR
    except ValueError:
        decision = Decision.UNCLEAR
    if decision is Decision.APPROVE and not words_allow_approval(text, action.action_type):
        return Verdict(Decision.UNCLEAR, source="model+guard")
    change = (call.input.get("change_request") or None) if call and decision is Decision.CHANGE else None
    return Verdict(decision, change, source="model")


# --- state changes (atomic, owner-scoped) ---


async def awaiting_decision(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> PendingAction | None:
    """The proposal in this conversation currently waiting for the user's answer."""
    statement = (
        select(PendingAction)
        .where(
            PendingAction.user_id == user_id,
            PendingAction.conversation_id == conversation_id,
            PendingAction.status == ActionStatus.PENDING,
        )
        .order_by(col(PendingAction.presented_at).desc().nulls_last(), col(PendingAction.updated_at).desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return (await session.exec(statement)).first()


async def reload(session: AsyncSession, action_id: uuid.UUID) -> PendingAction:
    statement = (
        select(PendingAction)
        .where(PendingAction.id == action_id)
        .execution_options(populate_existing=True)
    )
    return (await session.exec(statement)).one()


async def mark_presented(session: AsyncSession, action: PendingAction, window_minutes: int) -> None:
    """The proposal has just been shown and read out: start its approval window."""
    now = utcnow()
    action.status = ActionStatus.PENDING
    action.presented_at = now
    action.expires_at = now + timedelta(minutes=window_minutes)
    session.add(action)
    await session.flush()
    # updated_at is set by the database on UPDATE; load it now, while we can await.
    await session.refresh(action)


async def approve(
    session: AsyncSession, *, action_id: uuid.UUID, user_id: uuid.UUID, version: int
) -> bool:
    """Approve exactly `version`, only if it was presented and its window is still open."""
    statement = (
        update(PendingAction)
        .where(
            col(PendingAction.id) == action_id,
            col(PendingAction.user_id) == user_id,
            col(PendingAction.status) == ActionStatus.PENDING,
            col(PendingAction.version) == version,
            col(PendingAction.presented_at).is_not(None),
            col(PendingAction.expires_at) > func.now(),
        )
        .values(status=ActionStatus.APPROVED, approved_version=version, decided_at=func.now())
        .returning(col(PendingAction.id))
        .execution_options(synchronize_session=False)
    )
    approved = (await session.exec(statement)).first() is not None
    await session.commit()
    return approved


async def cancel(session: AsyncSession, *, action_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    statement = (
        update(PendingAction)
        .where(
            col(PendingAction.id) == action_id,
            col(PendingAction.user_id) == user_id,
            col(PendingAction.status) == ActionStatus.PENDING,
        )
        .values(status=ActionStatus.CANCELLED, decided_at=func.now())
        .returning(col(PendingAction.id))
        .execution_options(synchronize_session=False)
    )
    cancelled = (await session.exec(statement)).first() is not None
    await session.commit()
    return cancelled


async def execute(session: AsyncSession, action_id: uuid.UUID, user: UserContext) -> PendingAction:
    """Run an approved action once and record what really happened."""
    claim = (
        update(PendingAction)
        .where(
            col(PendingAction.id) == action_id,
            col(PendingAction.user_id) == user.id,
            col(PendingAction.status) == ActionStatus.APPROVED,
            col(PendingAction.approved_version) == col(PendingAction.version),
        )
        .values(status=ActionStatus.EXECUTING)
        .returning(col(PendingAction.id))
        .execution_options(synchronize_session=False)
    )
    claimed = (await session.exec(claim)).first() is not None
    # Committed BEFORE the provider call: if CoO crashes mid-call, the row says
    # "executing", and a restart marks it uncertain instead of running it again.
    await session.commit()
    action = await reload(session, action_id)
    if not claimed:
        return action  # already run (or no longer approved): never run twice

    executor = executors.EXECUTORS.get(action.action_type)
    result: dict[str, Any] | None = None
    if executor is None:
        status, error = ActionStatus.FAILED, "CoO doesn't know how to carry out this kind of action."
    elif executor.permission not in user.permissions:
        status, error = ActionStatus.FAILED, "You don't have permission to do that."
    else:
        try:
            result = await executor.run(action.proposal, action.idempotency_key, session)
            if executor.verify(result):
                status, error = ActionStatus.SUCCEEDED, None
            else:
                status, error = ActionStatus.UNCERTAIN, "The service answered, but didn't confirm it worked."
        except executors.ProviderRejected as exc:
            status, error = ActionStatus.FAILED, str(exc)
        except executors.ProviderTimeout as exc:
            status, error = ActionStatus.UNCERTAIN, str(exc)
        except Exception as exc:  # unknown failure: it may or may not have happened
            logger.exception("executor crashed action_id=%s", action_id)
            status, error = ActionStatus.UNCERTAIN, f"Something unexpected went wrong ({type(exc).__name__})."

    finish = (
        update(PendingAction)
        .where(col(PendingAction.id) == action_id, col(PendingAction.status) == ActionStatus.EXECUTING)
        .values(status=status, result=result, error=error, completed_at=func.now())
        .execution_options(synchronize_session=False)
    )
    await session.exec(finish)
    await session.commit()
    logger.info("action finished action_id=%s type=%s status=%s", action_id, action.action_type, status)
    return await reload(session, action_id)


async def recover_interrupted(session: AsyncSession) -> int:
    """At startup: anything still 'executing' was cut off mid-call. Never re-run it."""
    statement = (
        update(PendingAction)
        .where(col(PendingAction.status) == ActionStatus.EXECUTING)
        .values(
            status=ActionStatus.UNCERTAIN,
            error="CoO restarted while this was running, so it's unclear whether it happened. "
            "Check before trying again.",
            completed_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    result = await session.exec(statement)
    return result.rowcount or 0


# --- what CoO says ---

_QUESTIONS = {
    "email.send": "Shall I send it?",
    "meeting.cancel": "Shall I cancel it?",
    "leave.request": "Shall I submit it?",
}
DEMO_NOTE = " This was demo mode, so nothing outside CoO really changed."


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def confirmation(action: PendingAction) -> str:
    """The exact details read aloud, plus the approval question."""
    question = _QUESTIONS.get(action.action_type, "Shall I go ahead?")
    return f"To confirm, I'll {_lower_first(action.description)}. {question} Say yes, no, or tell me what to change."


def outcome_reply(action: PendingAction) -> str:
    proposal = action.proposal
    demo = DEMO_NOTE if (action.result or {}).get("mock") else ""
    if action.status is ActionStatus.SUCCEEDED:
        if action.action_type == "email.send":
            done = f"Done. Your email to {', '.join(proposal.get('to_names') or proposal['to'])} is sent."
        elif action.action_type == "meeting.cancel":
            done = f"Done. '{proposal['meeting']['title']}' is cancelled and the attendees are notified."
        elif action.action_type == "leave.request":
            done = (
                f"Done. Your leave request from {proposal['start_date']} to {proposal['end_date']} "
                "is submitted to your manager."
            )
        else:
            done = "Done."
        return done + demo
    if action.status is ActionStatus.FAILED:
        return f"That didn't go through. {action.error} Nothing was done."
    if action.status is ActionStatus.UNCERTAIN:
        return f"I couldn't confirm whether that went through. {action.error} Please check before trying again."
    return "There's nothing waiting for your approval any more."


@dataclass
class ApprovalOutcome:
    reply: str
    action: PendingAction
    cards: list[dict[str, Any]] = field(default_factory=list)


async def handle(
    session: AsyncSession,
    user: UserContext,
    action: PendingAction,
    verdict: Verdict,
    *,
    window_minutes: int,
) -> ApprovalOutcome:
    """Answer an approve / reject / show / unclear reply to a waiting proposal."""
    action_id = action.id

    async def present_again(prefix: str) -> ApprovalOutcome:
        fresh = await reload(session, action_id)
        await mark_presented(session, fresh, window_minutes)
        return ApprovalOutcome(f"{prefix} {confirmation(fresh)}", fresh, [proposal_card(fresh)])

    if verdict.decision is Decision.APPROVE:
        approved = await approve(session, action_id=action_id, user_id=user.id, version=action.version)
        if not approved:
            current = await reload(session, action_id)
            if current.status is ActionStatus.PENDING:  # the window had closed
                return await present_again(
                    f"That has been waiting more than {window_minutes} minutes, so here it is again."
                )
            return ApprovalOutcome(outcome_reply(current), current, [proposal_card(current)])
        done = await execute(session, action_id, user)
        return ApprovalOutcome(outcome_reply(done), done, [proposal_card(done)])

    if verdict.decision is Decision.REJECT:
        await cancel(session, action_id=action_id, user_id=user.id)
        current = await reload(session, action_id)
        return ApprovalOutcome(
            f"Okay, I won't {_lower_first(current.description)}. It's cancelled.",
            current,
            [proposal_card(current)],
        )

    if verdict.decision is Decision.SHOW:
        return await present_again("Here it is again.")

    # Unclear: ask again. Nothing changes, and silence or doubt never counts as a yes.
    return ApprovalOutcome(
        f"Sorry, I didn't catch a clear yes or no. {confirmation(action)}", action, [proposal_card(action)]
    )


def waiting_note(action: PendingAction) -> str:
    return (
        f"A proposal is waiting for the user's decision: {action.description}. Answer the user's "
        "current message normally. If it fits, add one short reminder that it's still waiting."
    )


def change_note(action: PendingAction, request: str) -> str:
    return (
        f"The user wants to change the proposal that is waiting for their decision: "
        f"{action.description}. Current details: {json.dumps(action.proposal, default=str)}. "
        f'Their request: "{request}". Call the same tool again with the complete updated details '
        "(that replaces the waiting proposal), or ask one short question if the change isn't clear."
    )
