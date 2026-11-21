"""The Groq (OpenAI-compatible) adapter: translation both ways, errors, and a full CoO turn."""

import asyncio
import json

import httpx2
import pytest

from app.agents.llm import ModelError
from app.agents.llm_openai import OpenAICompatibleChatModel, to_openai_messages
from app.models import Role

EMAIL = {"to": ["Akshay"], "subject": "Deployment delay", "body": "It moves to Friday."}


def groq(handler, name: str = "openai/gpt-oss-120b") -> OpenAICompatibleChatModel:
    return OpenAICompatibleChatModel(
        provider="Groq",
        base_url="https://api.groq.test/openai/v1",
        api_key="gsk_test",
        model=name,
        max_tokens=1024,
        effort="low",
        timeout=5,
        transport=httpx2.MockTransport(handler),
    )


def answer(message: dict, finish: str = "stop") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 12},
        },
    )


def ask(model: OpenAICompatibleChatModel, tools: list | None = None):
    return asyncio.run(
        model.respond(system="Be brief.", messages=[{"role": "user", "content": "Hi"}], tools=tools or [])
    )


def test_tool_loop_history_becomes_openai_messages() -> None:
    history = [
        {"role": "user", "content": "Email Akshay"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Drafting it."},
                {"type": "tool_use", "id": "call_1", "name": "mail_send_email", "input": EMAIL},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"status": "awaiting_approval"}'}]},
    ]

    out = to_openai_messages("You are CoO.", history)

    assert out[0] == {"role": "system", "content": "You are CoO."}
    assert out[1] == {"role": "user", "content": "Email Akshay"}
    assert out[2]["content"] == "Drafting it."
    assert out[2]["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "mail_send_email", "arguments": json.dumps(EMAIL)}}
    ]
    assert out[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "mail_send_email",
        "content": '{"status": "awaiting_approval"}',
    }


def test_request_and_tool_call_reply() -> None:
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return answer(
            {
                "content": None,
                "tool_calls": [
                    {"id": "call_9", "type": "function", "function": {"name": "web_get_news", "arguments": '{"topic": "tech"}'}}
                ],
            },
            finish="tool_calls",
        )

    tools = [{"name": "web_get_news", "description": "News", "input_schema": {"type": "object", "properties": {}}}]
    turn = ask(groq(handler), tools)

    body = json.loads(sent[0].content)
    assert str(sent[0].url) == "https://api.groq.test/openai/v1/chat/completions"
    assert sent[0].headers["authorization"] == "Bearer gsk_test"
    assert body["tools"] == [
        {"type": "function", "function": {"name": "web_get_news", "description": "News", "parameters": {"type": "object", "properties": {}}}}
    ]
    assert body["tool_choice"] == "auto"
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 1024
    assert [(c.id, c.name, c.input) for c in turn.tool_calls] == [("call_9", "web_get_news", {"topic": "tech"})]
    assert turn.content == [{"type": "tool_use", "id": "call_9", "name": "web_get_news", "input": {"topic": "tech"}}]
    assert turn.usage == {"input_tokens": 120, "output_tokens": 12}


def test_reasoning_never_reaches_the_spoken_reply() -> None:
    turn = ask(groq(lambda r: answer({"content": "<think>let me plan</think>Hello there.", "reasoning": "planning"})))

    assert turn.text == "Hello there."


def test_models_without_reasoning_controls_get_no_reasoning_effort() -> None:
    sent = []
    ask(groq(lambda r: sent.append(r) or answer({"content": "Hi"}), name="llama-3.3-70b-versatile"))

    assert "reasoning_effort" not in json.loads(sent[0].content)


def test_unparseable_arguments_go_back_through_tool_validation() -> None:
    bad = {"id": "c1", "type": "function", "function": {"name": "web_get_news", "arguments": "{not json"}}
    turn = ask(groq(lambda r: answer({"content": None, "tool_calls": [bad]}, "tool_calls")))

    assert turn.tool_calls[0].input == {"_unparseable_arguments": "{not json"}


def test_a_malformed_tool_call_is_retried_once() -> None:
    replies = [
        httpx2.Response(400, json={"error": {"message": "Failed to call a function. Please adjust your prompt.", "code": "tool_use_failed"}}),
        answer({"content": "Recovered."}),
    ]
    turn = ask(groq(lambda r: replies.pop(0)))

    assert turn.text == "Recovered."


def test_short_rate_limit_waits_then_retries_and_long_one_reports() -> None:
    replies = [httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}}), answer({"content": "Ok."})]
    assert ask(groq(lambda r: replies.pop(0))).text == "Ok."

    with pytest.raises(ModelError, match="rate limit reached; try again in about 42s"):  # longer than the 20s it waits out
        ask(groq(lambda r: httpx2.Response(429, headers={"retry-after": "42"}, json={"error": {"message": "slow down"}})))


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "rejected the API key"), (404, "has no model"), (500, "Groq error 500")],
)
def test_errors_become_model_errors(status: int, expected: str) -> None:
    with pytest.raises(ModelError, match=expected):
        ask(groq(lambda r: httpx2.Response(status, json={"error": {"message": "nope"}})))


def test_missing_key_is_a_setup_problem() -> None:
    model = OpenAICompatibleChatModel(
        provider="Groq", base_url="https://x", api_key="", model="m", max_tokens=1, effort="low", timeout=1
    )
    with pytest.raises(ModelError, match="no Groq API key"):
        ask(model)


@pytest.mark.integration
def test_a_full_coo_turn_through_the_groq_adapter(client, make_user, auth_headers) -> None:
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            call = {"id": "call_1", "type": "function", "function": {"name": "web_get_news", "arguments": "{}"}}
            return answer({"content": None, "tool_calls": [call]}, "tool_calls")
        return answer({"content": "Here are today's top stories, from demo data."})

    headers = auth_headers(make_user("ops@coo.example", Role.ADMIN))
    client.app.state.chat_model = groq(handler)
    conversation = client.post("/conversations", headers=headers).json()["id"]

    body = client.post(f"/conversations/{conversation}/turns", headers=headers, json={"text": "What's the news?"}).json()

    assert body["reply"] == "Here are today's top stories, from demo data."
    assert body["cards"][0]["type"] == "headlines"
    tool_message = requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert tool_message["name"] == "web_get_news"
    assert json.loads(tool_message["content"])["headlines"][0]["title"] == "Central bank holds interest rates steady"


def test_rate_limits_are_waited_out_repeatedly_within_the_budget() -> None:
    limited = lambda: httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}})  # noqa: E731
    replies = [limited(), limited(), limited(), answer({"content": "Made it."})]

    assert ask(groq(lambda r: replies.pop(0))).text == "Made it."
