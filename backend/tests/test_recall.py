"""Recall and resume: earlier conversations, "new conversation", reopening, summaries, checkpoints."""

import json
import uuid

import pytest
from sqlmodel import Session, select, text

from app.agents.summaries import summarize_if_due
from app.conversations import store
from app.db import Database
from app.models import Conversation, Message, MessageRole, Role
from scripted import ScriptedModel, say, use

pytestmark = pytest.mark.integration

EMAIL = {"to": ["Akshay"], "subject": "Deployment delay", "body": "Hi Akshay, the deployment moves to Friday."}


@pytest.fixture
def admin(make_user):
    return make_user("ops@coo.example", Role.ADMIN, name="Ops")


def talk(client, headers, conversation_id, words: str) -> dict:
    response = client.post(f"/conversations/{conversation_id}/turns", headers=headers, json={"text": words})
    assert response.status_code == 200
    return response.json()


def new_conversation(client, headers) -> str:
    return client.post("/conversations", headers=headers).json()["id"]


def earlier_conversation(run_db, user_id, *exchange: tuple[MessageRole, str]) -> uuid.UUID:
    async def _create(db: Database) -> uuid.UUID:
        async with db.sessionmaker() as session:
            conversation = await store.create_conversation(session, user_id)
            for role, words in exchange:
                await store.append_message(session, user_id, conversation.id, role, words)
            await session.commit()
            return conversation.id

    return run_db(_create)


def last_tool_result(model: ScriptedModel, call: int) -> dict:
    return json.loads(model.calls[call]["messages"][-1]["content"][0]["content"])


def test_where_did_we_stop_finds_the_latest_earlier_conversation(client, admin, auth_headers, run_db) -> None:
    headers = auth_headers(admin)
    earlier = earlier_conversation(
        run_db,
        admin.id,
        (MessageRole.USER, "Prepare an email to Akshay about the deployment delay"),
        (MessageRole.ASSISTANT, "The draft is ready. Would you like to review it?"),
    )
    client.app.state.chat_model = model = ScriptedModel(
        use(("conversations_find", {})), say("We were preparing an email to Akshay about the deployment delay.")
    )
    current = new_conversation(client, headers)

    talk(client, headers, current, "Where did we stop?")

    found = last_tool_result(model, 1)
    assert [c["conversation_id"] for c in found["conversations"]] == [str(earlier)]  # not the current one
    assert found["conversations"][0]["last_assistant_message"] == "The draft is ready. Would you like to review it?"


def test_topic_search_picks_the_right_conversation(client, admin, auth_headers, run_db) -> None:
    headers = auth_headers(admin)
    deployment = earlier_conversation(
        run_db, admin.id, (MessageRole.USER, "The deployment delay needs an email to leadership")
    )
    earlier_conversation(run_db, admin.id, (MessageRole.USER, "How much coffee is left in the pantry?"))
    client.app.state.chat_model = model = ScriptedModel(
        use(("conversations_find", {"topic": "deployment delay"})), say("Found it.")
    )

    talk(client, headers, new_conversation(client, headers), "Show me the conversation about the deployment delay")

    found = last_tool_result(model, 1)
    assert found["conversations"][0]["conversation_id"] == str(deployment)


def test_other_peoples_conversations_never_come_back(client, admin, make_user, auth_headers, run_db) -> None:
    other = make_user("other@coo.example", Role.ADMIN)
    theirs = earlier_conversation(run_db, other.id, (MessageRole.USER, "Secret deployment delay plans"))
    headers = auth_headers(admin)
    client.app.state.chat_model = model = ScriptedModel(
        use(
            ("conversations_find", {"topic": "deployment delay"}),
            ("conversations_read", {"conversation_id": str(theirs)}),
            ("conversations_resume", {"conversation_id": str(theirs)}),
        ),
        say("I couldn't find that."),
    )

    body = talk(client, headers, new_conversation(client, headers), "Find the deployment delay chat")

    found, read, resumed = (json.loads(r["content"]) for r in model.calls[1]["messages"][-1]["content"])
    assert found["found"] == 0
    assert read == {"error": "There's no such conversation."}
    assert resumed == {"error": "There's no such conversation."}
    assert body["switch_to_conversation_id"] is None


def test_previous_answer_is_read_word_for_word(client, admin, auth_headers, run_db) -> None:
    headers = auth_headers(admin)
    earlier = earlier_conversation(
        run_db,
        admin.id,
        (MessageRole.USER, "How many leave days do I get?"),
        (MessageRole.ASSISTANT, "According to the Leave and holidays policy, you get 20 days a year."),
    )
    client.app.state.chat_model = model = ScriptedModel(
        use(("conversations_read", {"conversation_id": str(earlier), "last": 2})), say("I said: ...")
    )

    talk(client, headers, new_conversation(client, headers), "Repeat your previous answer")

    messages = last_tool_result(model, 1)["messages"]
    assert messages[-1] == {
        "sequence": 2,
        "role": "assistant",
        "text": "According to the Leave and holidays policy, you get 20 days a year.",
        "at": messages[-1]["at"],
    }


def test_resume_reads_back_the_waiting_draft_then_yes_sends_it(client, admin, auth_headers, sync_engine) -> None:
    headers = auth_headers(admin)
    # Earlier: a draft was prepared, but the user left before approving it.
    client.app.state.chat_model = ScriptedModel(use(("mail_send_email", EMAIL)), say("I've drafted the email."))
    earlier = new_conversation(client, headers)
    talk(client, headers, earlier, "Prepare an email to Akshay about the deployment delay")

    # Hours later, in a fresh conversation.
    current = new_conversation(client, headers)
    client.app.state.chat_model = ScriptedModel(
        use(("conversations_resume", {"conversation_id": earlier})),
        say("We were preparing an email to Akshay about the deployment delay, and it isn't sent yet."),
    )
    body = talk(client, headers, current, "Let's continue where we left off")

    assert body["switch_to_conversation_id"] == earlier
    assert "To confirm, I'll send email to Akshay Rao" in body["reply"]
    assert body["pending_actions"][0]["status"] == "pending"

    # The user now continues in the earlier conversation, where the draft is waiting.
    client.app.state.chat_model = ScriptedModel()
    done = talk(client, headers, earlier, "Looks good. Send it.")

    assert done["pending_actions"][0]["status"] == "succeeded"
    with Session(sync_engine) as session:
        roles = session.exec(select(Message.role).where(Message.conversation_id == uuid.UUID(earlier))).all()
    assert MessageRole.SYSTEM in roles  # "The user resumed this conversation."


def test_new_conversation_starts_clean_without_the_model(client, admin, auth_headers, sync_engine) -> None:
    headers = auth_headers(admin)
    client.app.state.chat_model = model = ScriptedModel()
    current = new_conversation(client, headers)

    body = talk(client, headers, current, "New conversation, please")

    fresh = body["switch_to_conversation_id"]
    assert fresh and fresh != current
    assert body["reply"] == "Sure, let's start a new conversation."
    assert model.calls == []
    with Session(sync_engine) as session:
        assert session.get(Conversation, uuid.UUID(fresh)).message_count == 0


def test_reopening_after_a_switch_lands_in_the_new_conversation(client, admin, auth_headers) -> None:
    headers = auth_headers(admin)
    client.app.state.chat_model = ScriptedModel(say("Hello!"))
    first = client.post("/conversations/start", headers=headers).json()["conversation"]["id"]
    talk(client, headers, first, "Hi")

    fresh = talk(client, headers, first, "New conversation")["switch_to_conversation_id"]
    reopened = client.post("/conversations/start", headers=headers).json()

    assert reopened["conversation"]["id"] == fresh


def test_opening_the_app_continues_recent_work_but_not_after_a_long_gap(
    client, admin, auth_headers, sync_engine
) -> None:
    headers = auth_headers(admin)
    first = client.post("/conversations/start", headers=headers).json()
    assert first["continued"] is False

    client.app.state.chat_model = ScriptedModel(say("Hello!"))
    talk(client, headers, first["conversation"]["id"], "Hi")
    again = client.post("/conversations/start", headers=headers).json()
    assert again["continued"] is True
    assert again["conversation"]["id"] == first["conversation"]["id"]

    with sync_engine.begin() as conn:
        conn.execute(text("UPDATE conversations SET last_activity_at = now() - interval '3 hours'"))
    later = client.post("/conversations/start", headers=headers).json()
    assert later["continued"] is False
    assert later["conversation"]["id"] != first["conversation"]["id"]


def test_summary_is_refreshed_after_enough_messages(admin, run_db) -> None:
    conversation_id = earlier_conversation(
        run_db,
        admin.id,
        (MessageRole.USER, "Draft an email to Akshay"),
        (MessageRole.ASSISTANT, "The draft is ready."),
    )
    model = ScriptedModel(say("Drafting an email to Akshay; not sent yet."))

    async def _summarize(db: Database) -> str | None:
        await summarize_if_due(db, admin.id, conversation_id, model, every=2)
        async with db.sessionmaker() as session:
            return (await store.get_conversation(session, admin.id, conversation_id)).summary

    assert run_db(_summarize) == "Drafting an email to Akshay; not sent yet."
    assert "user: Draft an email to Akshay" in model.calls[0]["messages"][0]["content"]


def test_agent_steps_are_checkpointed_in_postgres(client, admin, auth_headers, sync_engine) -> None:
    headers = auth_headers(admin)
    client.app.state.chat_model = ScriptedModel(use(("web_get_news", {})), say("Here's the news."))
    conversation = new_conversation(client, headers)

    talk(client, headers, conversation, "What's the news?")

    with sync_engine.connect() as conn:
        checkpoints = conn.execute(
            text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"), {"thread": conversation}
        ).scalar()
    assert checkpoints >= 3  # start, after the model call, after the tools, after the reply
