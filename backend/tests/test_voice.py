"""Voice through Agora: starting / stopping CoO's agent, and Agora's calls into CoO."""

import asyncio
import json
import uuid

import httpx2
import pytest
from pydantic import SecretStr
from sqlmodel import Session, select

from app.api import voice as voice_api
from app.config import get_settings
from app.models import Role, VoiceSession, VoiceStatus
from app.voice.agora import VOICE_KEY_HEADER, AgoraClient
from app.voice.bridge import last_user_text, sse_reply
from scripted import ScriptedModel, say, use


class SlowModel(ScriptedModel):
    """A model that takes a while, like a tool turn or a rate-limit wait."""

    async def respond(self, **kwargs):
        await asyncio.sleep(0.3)
        return await super().respond(**kwargs)

PUBLIC_URL = "https://coo-test.ngrok-free.app"
SHARED_KEY = "voice-shared-secret"
APP_ID = "0123456789abcdef0123456789abcdef"


# --- the bridge (no database) ---


def test_last_user_text_reads_the_newest_user_message() -> None:
    messages = [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": [{"type": "text", "text": "What's"}, {"type": "text", "text": "the news?"}]},
    ]
    assert last_user_text(messages) == "What's the news?"
    assert last_user_text([{"role": "assistant", "content": "hi"}]) == ""


def test_reply_streams_as_openai_chunks_ending_with_done() -> None:
    lines = list(sse_reply("Here's the news. Rates are steady!"))

    assert lines[-1] == "data: [DONE]\n\n"
    chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Here's the news. Rates are steady!"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


# --- calls (test database, fake Agora) ---

integration = pytest.mark.integration


class FakeAgora:
    """Stands in for api.agora.io and records what CoO sends."""

    def __init__(self, refuse: bool = False) -> None:
        self.requests: list[httpx2.Request] = []
        self.refuse = refuse

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.refuse:
            return httpx2.Response(403, json={"detail": "forbidden", "reason": "invalid vendor key"})
        if request.url.path.endswith("/join"):
            return httpx2.Response(200, json={"agent_id": "agent-123", "create_ts": 1, "status": "RUNNING"})
        return httpx2.Response(200)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def voice_settings(test_settings):
    return test_settings.model_copy(
        update={
            "agora_app_id": APP_ID,
            "agora_app_certificate": SecretStr("fedcba9876543210fedcba9876543210"),
            "murf_api_key": SecretStr("murf-test-key"),
            "public_base_url": PUBLIC_URL,
            "custom_llm_api_key": SecretStr(SHARED_KEY),
        }
    )


@pytest.fixture
def agora(client, voice_settings) -> FakeAgora:
    fake = FakeAgora()
    client.app.state.agora = AgoraClient(transport=httpx2.MockTransport(fake.handler))
    client.app.dependency_overrides[get_settings] = lambda: voice_settings
    return fake


@pytest.fixture
def admin(make_user, auth_headers) -> dict[str, str]:
    return auth_headers(make_user("ops@coo.example", Role.ADMIN, name="Ops Lead"))


def start_call(client, headers) -> tuple[str, dict]:
    conversation = client.post("/conversations", headers=headers).json()["id"]
    response = client.post("/voice/sessions", headers=headers, json={"conversation_id": conversation})
    assert response.status_code == 200, response.text
    return conversation, response.json()


def agora_says(client, session_id: str, words: str, key: str = SHARED_KEY):
    return client.post(
        f"/voice/llm/{session_id}/chat/completions",
        headers={VOICE_KEY_HEADER: key},
        json={"model": "coo", "stream": True, "messages": [{"role": "user", "content": words}]},
    )


def streamed_text(response) -> str:
    lines = [line for line in response.text.split("\n\n") if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)


@integration
def test_without_keys_the_portal_uses_the_browser_voice(client, admin) -> None:
    config = client.get("/voice/config", headers=admin).json()
    conversation = client.post("/conversations", headers=admin).json()["id"]
    refused = client.post("/voice/sessions", headers=admin, json={"conversation_id": conversation})

    assert config["mode"] == "browser"
    assert "AGORA_APP_ID" in config["missing"]
    assert refused.status_code == 503
    assert "AGORA_APP_ID" in refused.json()["detail"]


@integration
def test_starting_a_call_starts_coos_agent_with_murf_and_our_llm(client, admin, agora) -> None:
    assert client.get("/voice/config", headers=admin).json() == {"mode": "agora", "missing": []}

    conversation, call = start_call(client, admin)

    [join] = agora.requests
    assert str(join.url) == f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{APP_ID}/join"
    assert join.headers["authorization"].startswith('agora token="007')
    properties = agora.body()["properties"]
    assert properties["channel"] == call["channel"]
    assert properties["agent_rtc_uid"] == "999001"
    assert properties["remote_rtc_uids"] == [str(call["uid"])]
    assert properties["llm"]["url"] == f"{PUBLIC_URL}/voice/llm/{call['session_id']}/chat/completions"
    assert properties["llm"]["headers"] == {VOICE_KEY_HEADER: SHARED_KEY}
    assert properties["llm"]["greeting_message"].startswith("Hi Ops,")
    assert properties["tts"]["vendor"] == "murf"
    assert properties["tts"]["params"]["api_key"] == "murf-test-key"
    assert call["token"].startswith("007")
    assert call["app_id"] == APP_ID
    assert call["conversation_id"] == conversation


@integration
def test_agora_refusing_the_agent_is_reported(client, admin, agora, sync_engine) -> None:
    agora.refuse = True
    conversation = client.post("/conversations", headers=admin).json()["id"]

    response = client.post("/voice/sessions", headers=admin, json={"conversation_id": conversation})

    assert response.status_code == 502
    assert "invalid vendor key" in response.json()["detail"]
    with Session(sync_engine) as session:
        assert session.exec(select(VoiceSession.status)).all() == [VoiceStatus.FAILED]


@integration
def test_agoras_call_runs_a_coo_turn_and_streams_the_reply(client, admin, agora) -> None:
    conversation, call = start_call(client, admin)
    client.app.state.chat_model = ScriptedModel(
        use(("web_get_news", {})), say("Here are today's top stories. Rates are steady.")
    )

    response = agora_says(client, call["session_id"], "What's the news today?")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert streamed_text(response) == "Here are today's top stories. Rates are steady."
    messages = client.get(f"/conversations/{conversation}/messages", headers=admin).json()
    said = [m for m in messages if m["role"] in ("user", "assistant")]
    assert [m["content"] for m in said] == ["What's the news today?", "Here are today's top stories. Rates are steady."]
    assert said[0]["meta"]["input"] == "voice"


@integration
def test_agoras_calls_need_the_shared_key_and_a_live_session(client, admin, agora) -> None:
    _, call = start_call(client, admin)
    client.app.state.chat_model = ScriptedModel()

    assert agora_says(client, call["session_id"], "hi", key="wrong").status_code == 401
    assert client.post(f"/voice/llm/{call['session_id']}/chat/completions", json={"messages": []}).status_code == 401
    assert agora_says(client, str(uuid.uuid4()), "hi").status_code == 404


@integration
def test_the_call_follows_a_new_conversation(client, admin, agora) -> None:
    conversation, call = start_call(client, admin)
    client.app.state.chat_model = ScriptedModel()

    response = agora_says(client, call["session_id"], "New conversation")

    assert streamed_text(response) == "Sure, let's start a new conversation."
    state = client.get(f"/voice/sessions/{call['session_id']}", headers=admin).json()
    assert state["conversation_id"] != conversation
    assert state["status"] == "active"


@integration
def test_stopping_the_call_stops_the_agent(client, admin, agora) -> None:
    _, call = start_call(client, admin)

    stopped = client.delete(f"/voice/sessions/{call['session_id']}", headers=admin).json()

    assert stopped["status"] == "ended"
    assert str(agora.requests[-1].url).endswith("/agents/agent-123/leave")
    assert agora_says(client, call["session_id"], "hello?").status_code == 404


@integration
def test_one_call_at_a_time(client, admin, agora, sync_engine) -> None:
    start_call(client, admin)
    start_call(client, admin)

    with Session(sync_engine) as session:
        statuses = sorted(s.value for s in session.exec(select(VoiceSession.status)).all())
    assert statuses == ["active", "ended"]
    assert any(str(r.url).endswith("/leave") for r in agora.requests)


@integration
def test_other_users_cannot_see_or_stop_a_call(client, admin, agora, make_user, auth_headers) -> None:
    _, call = start_call(client, admin)
    other = auth_headers(make_user("other@coo.example", Role.ADMIN))

    assert client.get(f"/voice/sessions/{call['session_id']}", headers=other).status_code == 404
    assert client.delete(f"/voice/sessions/{call['session_id']}", headers=other).status_code == 404


@integration
def test_a_slow_turn_says_one_moment_first(client, admin, agora, monkeypatch) -> None:
    monkeypatch.setattr(voice_api, "FILLER_AFTER_SECONDS", 0.05)
    _, call = start_call(client, admin)
    client.app.state.chat_model = SlowModel(say("Here's the news."))

    response = agora_says(client, call["session_id"], "What's the news?")

    assert streamed_text(response) == "One moment. Here's the news."


@integration
def test_a_quick_turn_has_no_filler(client, admin, agora) -> None:
    _, call = start_call(client, admin)
    client.app.state.chat_model = ScriptedModel(say("Hello!"))

    assert streamed_text(agora_says(client, call["session_id"], "Hi")) == "Hello!"
