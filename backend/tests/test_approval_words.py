"""How replies to a waiting proposal are read (no database)."""

import asyncio

import pytest

from app.agents.approvals import Decision, decide, decide_from_words, words_allow_approval
from app.agents.llm import ModelError
from app.models import PendingAction
from scripted import ScriptedModel, use


def proposal(action_type: str = "email.send") -> PendingAction:
    return PendingAction(
        conversation_id=None,  # type: ignore[arg-type]
        user_id=None,  # type: ignore[arg-type]
        action_type=action_type,
        description="Send email to Akshay Rao. Subject: Deployment delay",
        proposal={},
    )


@pytest.mark.parametrize(
    "text", ["Yes", "yes, send it please.", "Go ahead!", "Looks good. Send it.", "OK send it", "Yes, thank you"]
)
def test_clear_yes_is_recognised_without_the_model(text: str) -> None:
    assert decide_from_words(text, "email.send").decision is Decision.APPROVE


@pytest.mark.parametrize("text", ["No", "No, don't send it.", "cancel it", "Never mind", "no thank you"])
def test_clear_no_is_recognised_without_the_model(text: str) -> None:
    assert decide_from_words(text, "email.send").decision is Decision.REJECT


def test_show_and_change_phrases() -> None:
    assert decide_from_words("Read it again", "email.send").decision is Decision.SHOW
    assert decide_from_words("Change it", "email.send").decision is Decision.CHANGE


def test_cancel_it_is_ambiguous_when_the_proposal_is_a_cancellation() -> None:
    assert decide_from_words("cancel it", "meeting.cancel") is None
    assert decide_from_words("yes", "meeting.cancel").decision is Decision.APPROVE


@pytest.mark.parametrize(
    ("text", "action_type", "allowed"),
    [
        ("That looks good, go ahead and send it to him", "email.send", True),
        ("Send it tomorrow", "email.send", False),
        ("No problem, go ahead", "email.send", False),  # errs on the side of asking again
        ("Yes but change the subject", "email.send", False),
        ("Maybe send it later", "email.send", False),
        ("Yes, cancel the meeting", "meeting.cancel", True),
        ("Yes, cancel the meeting", "email.send", False),
    ],
)
def test_words_must_also_allow_an_approval(text: str, action_type: str, allowed: bool) -> None:
    assert words_allow_approval(text, action_type) is allowed


def test_model_approval_without_a_clear_yes_becomes_unclear() -> None:
    model = ScriptedModel(use(("record_decision", {"decision": "approve"})))

    verdict = asyncio.run(decide(model, "Maybe send it later?", proposal()))

    assert verdict.decision is Decision.UNCLEAR
    assert verdict.source == "model+guard"


def test_model_reads_a_change_request() -> None:
    model = ScriptedModel(
        use(("record_decision", {"decision": "change", "change_request": "make it shorter"}))
    )

    verdict = asyncio.run(decide(model, "Could you make it a bit shorter?", proposal()))

    assert verdict.decision is Decision.CHANGE
    assert verdict.change_request == "make it shorter"
    # The classifier sees only the proposal and the user's words.
    assert model.calls[0]["messages"] == [{"role": "user", "content": "Could you make it a bit shorter?"}]


def test_model_outage_means_asking_again_not_approving() -> None:
    verdict = asyncio.run(decide(ScriptedModel(ModelError("down")), "That works for me", proposal()))

    assert verdict.decision is Decision.UNCLEAR
