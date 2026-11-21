"""The approval flow end to end: propose -> read back -> yes / no / change -> run once."""

import asyncio
import dataclasses
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select, text

from app.agents import approvals, executors
from app.config import get_settings
from app.db import Database
from app.main import create_app
from app.models import ActionStatus, PendingAction, Role
from scripted import ScriptedModel, say, use

pytestmark = pytest.mark.integration

EMAIL = {"to": ["Akshay"], "subject": "Deployment delay", "body": "Hi Akshay, the deployment moves to Friday."}


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Every email the (mock) mail provider actually receives."""
    calls: list[dict] = []
    original = executors.EXECUTORS["email.send"]

    async def counting(proposal, key, session=None):
        calls.append(proposal)
        return await original.run(proposal, key, session)

    monkeypatch.setitem(executors.EXECUTORS, "email.send", dataclasses.replace(original, run=counting))
    return calls


@pytest.fixture
def admin(client, make_user, auth_headers) -> dict[str, str]:
    return auth_headers(make_user("ops@coo.example", Role.ADMIN, name="Ops"))


def talk(client, headers, conversation_id: str, words: str) -> dict:
    response = client.post(f"/conversations/{conversation_id}/turns", headers=headers, json={"text": words})
    assert response.status_code == 200
    return response.json()


def propose_email(client, headers, email: dict = EMAIL) -> tuple[str, dict]:
    """Start a conversation and get an email proposal read back. Returns (conversation, action)."""
    client.app.state.chat_model = ScriptedModel(use(("mail_send_email", email)), say("I've drafted the email."))
    conversation = client.post("/conversations", headers=headers).json()["id"]
    body = talk(client, headers, conversation, "Email Akshay about the deployment delay")
    [action] = body["pending_actions"]
    return conversation, action


def status_of(sync_engine, action_id: str) -> PendingAction:
    with Session(sync_engine) as session:
        return session.exec(select(PendingAction).where(PendingAction.id == action_id)).one()


def test_proposal_is_read_back_with_exact_details_and_a_window(client, admin) -> None:
    client.app.state.chat_model = ScriptedModel(use(("mail_send_email", EMAIL)), say("I've drafted the email."))
    conversation = client.post("/conversations", headers=admin).json()["id"]

    body = talk(client, admin, conversation, "Email Akshay about the deployment delay")

    [action] = body["pending_actions"]
    assert action["status"] == "pending"
    expires_in = datetime.fromisoformat(action["expires_at"]) - datetime.now().astimezone()
    assert timedelta(minutes=14) < expires_in <= timedelta(minutes=15)
    assert body["reply"] == (
        "I've drafted the email. To confirm, I'll send email to Akshay Rao (akshay@alrium.example). "
        "Subject: Deployment delay. Shall I send it? Say yes, no, or tell me what to change."
    )


def test_yes_runs_the_action_exactly_once(client, admin, sent) -> None:
    conversation, action = propose_email(client, admin)
    client.app.state.chat_model = model = ScriptedModel()  # a plain yes needs no model

    body = talk(client, admin, conversation, "Yes, send it please.")

    assert body["reply"] == (
        "Done. Your email to Akshay Rao is sent. This was demo mode, so nothing outside CoO really changed."
    )
    [done] = body["pending_actions"]
    assert done["status"] == "succeeded"
    assert done["result"]["message_id"].startswith("mock-")
    assert len(sent) == 1
    assert model.calls == []

    # Another yes finds nothing waiting: it's an ordinary message, and nothing runs again.
    client.app.state.chat_model = ScriptedModel(say("There's nothing waiting for approval."))
    talk(client, admin, conversation, "yes")
    assert len(sent) == 1


def test_no_cancels_and_nothing_runs(client, admin, sent, sync_engine) -> None:
    conversation, action = propose_email(client, admin)

    body = talk(client, admin, conversation, "No, don't send it.")

    assert body["reply"].startswith("Okay, I won't send email to Akshay Rao")
    assert status_of(sync_engine, action["id"]).status is ActionStatus.CANCELLED
    assert sent == []


def test_hesitant_reply_never_approves_even_if_the_model_says_so(client, admin, sent, sync_engine) -> None:
    conversation, action = propose_email(client, admin)
    client.app.state.chat_model = ScriptedModel(use(("record_decision", {"decision": "approve"})))

    body = talk(client, admin, conversation, "Maybe send it later?")

    assert body["reply"].startswith("Sorry, I didn't catch a clear yes or no.")
    assert status_of(sync_engine, action["id"]).status is ActionStatus.PENDING
    assert sent == []


def test_change_makes_a_new_version_that_needs_a_fresh_yes(client, admin, sent, run_db) -> None:
    conversation, action = propose_email(client, admin)
    client.app.state.chat_model = model = ScriptedModel(
        use(("record_decision", {"decision": "change", "change_request": "subject: Deployment moved to Friday"})),
        use(("mail_send_email", {**EMAIL, "subject": "Deployment moved to Friday"})),
        say("I've updated the subject."),
    )

    body = talk(client, admin, conversation, "Change the subject to Deployment moved to Friday")

    [revised] = body["pending_actions"]
    assert revised["id"] == action["id"]  # the same proposal, changed in place
    assert revised["version"] == 2
    assert revised["status"] == "pending"
    assert "Subject: Deployment moved to Friday. Shall I send it?" in body["reply"]
    assert "wants to change the proposal" in model.calls[1]["system"]
    assert sent == []

    # An approval of the old version can't be applied any more.
    async def approve_old_version(db: Database) -> bool:
        async with db.sessionmaker() as session:
            row = (await session.exec(select(PendingAction).where(PendingAction.id == action["id"]))).one()
            return await approvals.approve(session, action_id=row.id, user_id=row.user_id, version=1)

    assert run_db(approve_old_version) is False

    talk(client, admin, conversation, "yes")
    assert [email["subject"] for email in sent] == ["Deployment moved to Friday"]


def test_late_yes_shows_the_proposal_again_instead_of_running_it(client, admin, sent, sync_engine) -> None:
    conversation, action = propose_email(client, admin)
    with sync_engine.begin() as conn:
        conn.execute(text("UPDATE pending_actions SET expires_at = now() - interval '1 minute'"))

    body = talk(client, admin, conversation, "yes")

    assert body["reply"].startswith("That has been waiting more than 15 minutes, so here it is again.")
    assert status_of(sync_engine, action["id"]).status is ActionStatus.PENDING
    assert sent == []

    talk(client, admin, conversation, "yes")  # inside the new window
    assert len(sent) == 1


def test_show_it_again_reads_the_proposal_back(client, admin, sent) -> None:
    conversation, _ = propose_email(client, admin)

    body = talk(client, admin, conversation, "Read it again")

    assert body["reply"].startswith("Here it is again. To confirm, I'll send email to Akshay Rao")
    assert sent == []


def test_rejected_delivery_is_reported_as_failed(client, admin, sync_engine) -> None:
    conversation, action = propose_email(client, admin, {**EMAIL, "to": ["bounce@alrium.example"]})

    body = talk(client, admin, conversation, "yes")

    assert body["reply"].startswith("That didn't go through. The mail server rejected it")
    row = status_of(sync_engine, action["id"])
    assert row.status is ActionStatus.FAILED
    assert "mailbox" in row.error


def test_unconfirmed_delivery_is_reported_as_uncertain(client, admin, sync_engine) -> None:
    conversation, action = propose_email(client, admin, {**EMAIL, "to": ["slow@alrium.example"]})

    body = talk(client, admin, conversation, "yes")

    assert body["reply"].startswith("I couldn't confirm whether that went through.")
    assert status_of(sync_engine, action["id"]).status is ActionStatus.UNCERTAIN


def test_unrelated_question_leaves_the_proposal_waiting(client, admin, sync_engine) -> None:
    conversation, action = propose_email(client, admin)
    client.app.state.chat_model = model = ScriptedModel(
        use(("record_decision", {"decision": "unrelated"})),
        say("You have two meetings today. Your email draft is still waiting."),
    )

    talk(client, admin, conversation, "What's on my calendar today?")

    assert status_of(sync_engine, action["id"]).status is ActionStatus.PENDING
    assert "waiting for the user's decision" in model.calls[1]["system"]


def test_waiting_proposal_survives_a_restart_and_runs_once(
    client, admin, sent, test_database_url, test_settings
) -> None:
    conversation, action = propose_email(client, admin)

    restarted = create_app(database=Database(test_database_url), chat_model=ScriptedModel())
    restarted.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(restarted, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as again:
        body = talk(again, admin, conversation, "yes")
        talk_again = again.post(
            f"/conversations/{conversation}/turns", headers=admin, json={"text": "yes"}
        )

    assert body["pending_actions"][0]["status"] == "succeeded"
    assert talk_again.status_code == 200  # nothing is waiting now: just an ordinary message
    assert len(sent) == 1


def test_action_cut_off_mid_run_becomes_uncertain_on_restart(
    client, admin, sync_engine, test_database_url, test_settings
) -> None:
    _, action = propose_email(client, admin)
    with sync_engine.begin() as conn:
        conn.execute(text("UPDATE pending_actions SET status = 'executing'"))

    restarted = create_app(database=Database(test_database_url))
    restarted.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(restarted, backend_options={"loop_factory": asyncio.SelectorEventLoop}):
        pass  # startup is enough

    row = status_of(sync_engine, action["id"])
    assert row.status is ActionStatus.UNCERTAIN
    assert "restarted" in row.error


def test_executor_rechecks_permission(client, make_user, auth_headers, run_db, sync_engine) -> None:
    # An employee somehow has an email proposal waiting (they can't create one normally).
    employee = make_user("emp@coo.example", Role.EMPLOYEE)
    headers = auth_headers(employee)
    conversation = client.post("/conversations", headers=headers).json()["id"]

    async def plant(db: Database) -> str:
        from app.conversations import store

        async with db.sessionmaker() as session:
            action = await store.create_pending_action(
                session, employee.id, conversation, action_type="email.send",
                description="Send email to Akshay Rao", proposal={"to": ["akshay@alrium.example"], "subject": "x", "body": "y"},
            )  # fmt: skip
            await approvals.mark_presented(session, action, 15)
            await session.commit()
            return str(action.id)

    action_id = run_db(plant)
    client.app.state.chat_model = ScriptedModel()

    body = talk(client, headers, conversation, "yes")

    assert body["reply"].startswith("That didn't go through. You don't have permission")
    assert status_of(sync_engine, action_id).status is ActionStatus.FAILED
