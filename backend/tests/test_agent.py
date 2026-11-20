"""The central agent end to end: API turn -> LangGraph loop -> tools -> stored messages."""

import json

import pytest

from app.agents.graph import STEP_LIMIT_REPLY
from app.agents.llm import ModelError
from app.agents.turn import ERROR_REPLY
from app.config import get_settings
from app.models import Role
from scripted import ScriptedModel, say, use

pytestmark = pytest.mark.integration

EMAIL_TO_AKSHAY = {
    "to": ["Akshay"],
    "subject": "Deployment delay",
    "body": "Hi Akshay, the deployment moves to Friday.",
}


def start(client, headers) -> str:
    return client.post("/conversations", headers=headers).json()["id"]


def say_to(client, headers, conversation_id: str, text: str):
    return client.post(
        f"/conversations/{conversation_id}/turns", headers=headers, json={"text": text}
    )


def stored(client, headers, conversation_id: str) -> list[dict]:
    return client.get(f"/conversations/{conversation_id}/messages", headers=headers).json()


def last_tool_results(model: ScriptedModel, call: int) -> list[dict]:
    return model.calls[call]["messages"][-1]["content"]


def test_plain_question_is_answered_without_tools(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("ada@coo.example", Role.ADMIN, name="Ada"))
    client.app.state.chat_model = model = ScriptedModel(say("Hi Ada, how can I help?"))
    conversation = start(client, headers)

    response = say_to(client, headers, conversation, "Hello")

    assert response.status_code == 200
    assert response.json()["reply"] == "Hi Ada, how can I help?"
    assert response.json()["error"] is False
    assert [m["role"] for m in stored(client, headers, conversation)] == ["user", "assistant"]
    assert len(model.calls) == 1
    assert "Ada (admin)" in model.calls[0]["system"]
    assert model.calls[0]["messages"] == [{"role": "user", "content": "Hello"}]


def test_news_request_goes_through_the_web_specialist(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("ben@coo.example", Role.EMPLOYEE))
    client.app.state.chat_model = model = ScriptedModel(
        use(("web_get_news", {})),
        say("Here are today's top stories, from sample data."),
    )
    conversation = start(client, headers)

    body = say_to(client, headers, conversation, "What's the news for today?").json()

    assert body["reply"] == "Here are today's top stories, from sample data."
    assert body["cards"][0]["type"] == "headlines"
    messages = stored(client, headers, conversation)
    assert [m["role"] for m in messages] == ["user", "tool", "assistant"]
    assert messages[1]["meta"]["specialist"] == "web"
    assert messages[2]["meta"]["specialists"] == ["web"]
    assert messages[2]["meta"]["mock"] is True
    assert messages[2]["meta"]["model_calls"] == 2
    [result] = last_tool_results(model, 1)
    assert result["type"] == "tool_result"
    assert json.loads(result["content"])["mock"] is True


def test_sending_email_only_creates_a_proposal(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("cy@coo.example", Role.ADMIN))
    client.app.state.chat_model = model = ScriptedModel(
        use(("mail_send_email", EMAIL_TO_AKSHAY)),
        say("I've drafted an email to Akshay about the delay. Shall I send it?"),
    )
    conversation = start(client, headers)

    body = say_to(client, headers, conversation, "Email Akshay about the deployment delay").json()

    [action] = body["pending_actions"]
    assert action["action_type"] == "email.send"
    assert action["status"] == "requested"
    assert action["proposal"]["to"] == ["akshay@alrium.example"]
    assert body["cards"][0]["type"] == "proposal"
    [result] = last_tool_results(model, 1)
    assert json.loads(result["content"])["status"] == "awaiting_approval"
    listed = client.get(f"/conversations/{conversation}/pending-actions", headers=headers).json()
    assert [a["id"] for a in listed] == [action["id"]]


def test_employee_never_sees_or_runs_admin_tools(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("dee@coo.example", Role.EMPLOYEE))
    # The model "tries" an admin tool anyway, as a prompt injection might make it.
    client.app.state.chat_model = model = ScriptedModel(
        use(("mail_send_email", EMAIL_TO_AKSHAY)),
        say("I'm sorry, I can't send email for you."),
    )
    conversation = start(client, headers)

    body = say_to(client, headers, conversation, "Send Akshay an email").json()

    assert "mail_send_email" not in model.calls[0]["tools"]
    assert "web_get_news" in model.calls[0]["tools"]
    [result] = last_tool_results(model, 1)
    assert result["is_error"] is True
    assert "Permission denied" in result["content"]
    assert body["pending_actions"] == []
    assert client.get(f"/conversations/{conversation}/pending-actions", headers=headers).json() == []


def test_one_turn_can_use_two_specialists(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("eve@coo.example", Role.EMPLOYEE))
    client.app.state.chat_model = model = ScriptedModel(
        use(("web_get_news", {}), ("company_search_knowledge", {"query": "annual leave"})),
        say("Here's the news, and you get 20 days of leave a year."),
    )
    conversation = start(client, headers)

    say_to(client, headers, conversation, "News, and how much leave do I get?")

    results = last_tool_results(model, 1)
    assert len(results) == 2  # both results in ONE message
    assistant = stored(client, headers, conversation)[-1]
    assert assistant["meta"]["specialists"] == ["company_info", "web"]


def test_model_failure_gives_an_honest_error_reply(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("fay@coo.example", Role.EMPLOYEE))
    client.app.state.chat_model = ScriptedModel(ModelError("model is down"))
    conversation = start(client, headers)

    response = say_to(client, headers, conversation, "Hello?")

    assert response.status_code == 200
    assert response.json()["error"] is True
    assert response.json()["reply"] == ERROR_REPLY
    messages = stored(client, headers, conversation)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["meta"]["error"] is True


def test_failed_turn_leaves_no_orphan_proposal(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("gus@coo.example", Role.ADMIN))
    client.app.state.chat_model = ScriptedModel(
        use(("mail_send_email", EMAIL_TO_AKSHAY)), ModelError("model is down")
    )
    conversation = start(client, headers)

    body = say_to(client, headers, conversation, "Email Akshay").json()

    assert body["error"] is True
    assert body["pending_actions"] == []
    assert client.get(f"/conversations/{conversation}/pending-actions", headers=headers).json() == []


def test_runaway_tool_loop_is_stopped(client, make_user, auth_headers, test_settings) -> None:
    headers = auth_headers(make_user("hal@coo.example", Role.EMPLOYEE))
    client.app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(
        update={"agent_max_steps": 2}
    )
    client.app.state.chat_model = model = ScriptedModel(
        use(("web_get_news", {})), use(("web_get_news", {}))
    )
    conversation = start(client, headers)

    body = say_to(client, headers, conversation, "Keep checking the news").json()

    assert body["reply"] == STEP_LIMIT_REPLY
    assert len(model.calls) == 2


def test_earlier_messages_are_sent_as_context(client, make_user, auth_headers) -> None:
    headers = auth_headers(make_user("ivy@coo.example", Role.EMPLOYEE))
    client.app.state.chat_model = model = ScriptedModel(
        say("Sure. Which topic?"), say("Here's the technology news.")
    )
    conversation = start(client, headers)

    say_to(client, headers, conversation, "I want some news")
    say_to(client, headers, conversation, "Technology")

    assert model.calls[1]["messages"] == [
        {"role": "user", "content": "I want some news"},
        {"role": "assistant", "content": "Sure. Which topic?"},
        {"role": "user", "content": "Technology"},
    ]


def test_cannot_talk_in_someone_elses_conversation(client, make_user, auth_headers) -> None:
    owner = auth_headers(make_user("jo@coo.example", Role.EMPLOYEE))
    intruder = auth_headers(make_user("kim@coo.example", Role.ADMIN))
    client.app.state.chat_model = model = ScriptedModel()
    conversation = start(client, owner)

    response = say_to(client, intruder, conversation, "What did they say?")

    assert response.status_code == 404
    assert model.calls == []
