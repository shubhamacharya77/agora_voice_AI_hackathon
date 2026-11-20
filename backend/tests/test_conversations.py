"""Conversation storage: API reads, privacy, ordering, titles, summaries, pending actions."""

import asyncio
import uuid

import pytest

from app.conversations import store
from app.conversations.store import ConversationNotFound
from app.db import Database
from app.models import ActionStatus, MessageRole, Role

pytestmark = pytest.mark.integration


async def _new_conversation(db: Database, user_id: uuid.UUID, title: str | None = None) -> uuid.UUID:
    async with db.sessionmaker() as session:
        conversation = await store.create_conversation(session, user_id, title=title)
        await session.commit()
        return conversation.id


async def _append(db: Database, user_id: uuid.UUID, conversation_id: uuid.UUID, *messages) -> None:
    async with db.sessionmaker() as session:
        for role, content, *meta in messages:
            await store.append_message(
                session, user_id, conversation_id, role, content, meta=meta[0] if meta else None
            )
        await session.commit()


# --- API ---


def test_create_list_and_get_a_conversation(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("ada@coo.example", Role.ADMIN))

    created = client.post("/conversations", headers=headers)
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "New conversation"
    assert conversation["status"] == "active"
    assert conversation["message_count"] == 0

    listed = client.get("/conversations", headers=headers).json()
    assert [c["id"] for c in listed] == [conversation["id"]]
    detail = client.get(f"/conversations/{conversation['id']}", headers=headers)
    assert detail.status_code == 200


def test_conversation_endpoints_require_login(client) -> None:
    assert client.get("/conversations").status_code == 401
    assert client.post("/conversations").status_code == 401


def test_conversations_are_private_to_their_owner(client, make_user, auth_headers, run_db) -> None:
    owner = make_user("owner@coo.example", Role.EMPLOYEE)
    other = make_user("other@coo.example", Role.ADMIN)  # even an admin can't read it
    conversation_id = run_db(lambda db: _new_conversation(db, owner.id))
    run_db(lambda db: _append(db, owner.id, conversation_id, (MessageRole.USER, "my leave plans")))
    other_headers = auth_headers(other)

    assert client.get("/conversations", headers=other_headers).json() == []
    for path in ("", "/messages", "/pending-actions"):
        response = client.get(f"/conversations/{conversation_id}{path}", headers=other_headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "Conversation not found"}

    async def _other_appends(db: Database) -> None:
        async with db.sessionmaker() as session:
            await store.append_message(session, other.id, conversation_id, MessageRole.USER, "hi")

    with pytest.raises(ConversationNotFound):
        run_db(_other_appends)


def test_list_is_most_recent_activity_first(client, make_user, auth_headers, run_db) -> None:
    user = make_user("ben@coo.example", Role.EMPLOYEE)
    older = run_db(lambda db: _new_conversation(db, user.id, "older"))
    newer = run_db(lambda db: _new_conversation(db, user.id, "newer"))
    # New activity in the older conversation moves it to the top.
    run_db(lambda db: _append(db, user.id, older, (MessageRole.USER, "back to this one")))

    listed = client.get("/conversations", headers=auth_headers(user)).json()

    assert [c["id"] for c in listed] == [str(older), str(newer)]


def test_messages_come_back_in_order_with_paging_and_meta(
    client, make_user, auth_headers, run_db
) -> None:
    user = make_user("cy@coo.example", Role.EMPLOYEE)
    conversation_id = run_db(lambda db: _new_conversation(db, user.id))
    run_db(
        lambda db: _append(
            db,
            user.id,
            conversation_id,
            (MessageRole.USER, "What's the news today?"),
            (MessageRole.ASSISTANT, "Here are three headlines.", {"spoken_text": "Three headlines."}),
            (MessageRole.USER, "Read the first one"),
            (MessageRole.ASSISTANT, "The first headline is..."),
        )
    )
    headers = auth_headers(user)

    everything = client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()
    page = client.get(
        f"/conversations/{conversation_id}/messages?after=1&limit=2", headers=headers
    ).json()

    assert [m["sequence"] for m in everything] == [1, 2, 3, 4]
    assert [m["role"] for m in everything] == ["user", "assistant", "user", "assistant"]
    assert everything[1]["meta"] == {"spoken_text": "Three headlines."}
    assert [m["sequence"] for m in page] == [2, 3]


# --- store ---


def test_first_user_message_becomes_the_title(make_user, run_db) -> None:
    user = make_user("dee@coo.example", Role.EMPLOYEE)
    conversation_id = run_db(lambda db: _new_conversation(db, user.id))
    run_db(
        lambda db: _append(
            db,
            user.id,
            conversation_id,
            (MessageRole.ASSISTANT, "Hi, I'm CoO. How can I help?"),
            (MessageRole.USER, "Prepare an email to Akshay about the deployment delay"),
            (MessageRole.USER, "Make it short"),
        )
    )

    async def _read(db: Database):
        async with db.sessionmaker() as session:
            return await store.require_conversation(session, user.id, conversation_id)

    conversation = run_db(_read)
    assert conversation.title == "Prepare an email to Akshay about the deployment delay"
    assert conversation.message_count == 3


def test_concurrent_appends_never_share_a_sequence_number(make_user, run_db) -> None:
    user = make_user("eve@coo.example", Role.EMPLOYEE)
    conversation_id = run_db(lambda db: _new_conversation(db, user.id))

    async def _race(db: Database) -> list[int]:
        async def _one(i: int) -> int:
            async with db.sessionmaker() as session:
                message = await store.append_message(
                    session, user.id, conversation_id, MessageRole.USER, f"message {i}"
                )
                await session.commit()
                return message.sequence

        return list(await asyncio.gather(*(_one(i) for i in range(10))))

    sequences = run_db(_race)

    assert sorted(sequences) == list(range(1, 11))


def test_summary_is_stored_without_touching_messages(client, make_user, auth_headers, run_db) -> None:
    user = make_user("fay@coo.example", Role.ADMIN)
    conversation_id = run_db(lambda db: _new_conversation(db, user.id))
    run_db(
        lambda db: _append(
            db,
            user.id,
            conversation_id,
            (MessageRole.USER, "Draft an email to Akshay"),
            (MessageRole.ASSISTANT, "The draft is ready."),
        )
    )

    async def _summarize(db: Database) -> None:
        async with db.sessionmaker() as session:
            await store.set_summary(
                session, user.id, conversation_id, "Drafting an email to Akshay.", through_sequence=2
            )
            await session.commit()

    run_db(_summarize)
    headers = auth_headers(user)

    detail = client.get(f"/conversations/{conversation_id}", headers=headers).json()
    messages = client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()
    assert detail["summary"] == "Drafting an email to Akshay."
    assert [m["content"] for m in messages] == ["Draft an email to Akshay", "The draft is ready."]


def test_pending_action_is_recorded_but_nothing_runs(client, make_user, auth_headers, run_db) -> None:
    user = make_user("gus@coo.example", Role.ADMIN)
    other = make_user("hal@coo.example", Role.ADMIN)
    conversation_id = run_db(lambda db: _new_conversation(db, user.id))
    proposal = {"to": ["akshay@example.com"], "subject": "Deployment delay", "body": "Hi Akshay, ..."}

    async def _propose(db: Database):
        async with db.sessionmaker() as session:
            action = await store.create_pending_action(
                session,
                user.id,
                conversation_id,
                action_type="email.send",
                description="Send email to Akshay: Deployment delay",
                proposal=proposal,
            )
            await session.commit()
            return action

    action = run_db(_propose)
    assert action.status is ActionStatus.REQUESTED
    assert action.version == 1
    assert action.idempotency_key is not None
    assert action.result is None

    listed = client.get(
        f"/conversations/{conversation_id}/pending-actions", headers=auth_headers(user)
    ).json()
    assert [a["id"] for a in listed] == [str(action.id)]
    assert listed[0]["proposal"] == proposal
    assert listed[0]["status"] == "requested"

    async def _list_for_other(db: Database):
        async with db.sessionmaker() as session:
            return await store.list_pending_actions(session, other.id)

    assert run_db(_list_for_other) == []
