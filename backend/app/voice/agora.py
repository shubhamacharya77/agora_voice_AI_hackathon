"""Agora Conversational AI Engine: tokens, and starting / stopping CoO's voice agent."""

import base64
import logging
import uuid
from typing import Any

import httpx2
from agora_agent.agentkit.token import generate_convo_ai_token, generate_rtc_token

from app.config import Settings, secret

logger = logging.getLogger(__name__)

# The header (set through the documented `llm.headers` field) that carries our shared
# secret on every call Agora makes to our custom-LLM endpoint.
VOICE_KEY_HEADER = "X-CoO-Voice-Key"
TOKEN_SECONDS = 3600  # one call; well under Agora's 24-hour maximum


class AgoraError(RuntimeError):
    """Agora refused or failed a request."""


def agent_token(settings: Settings, channel: str, agent_uid: int) -> str:
    """RTC + RTM + ConvoAI token for the agent; it also authenticates the REST call."""
    return generate_convo_ai_token(
        app_id=settings.agora_app_id or "",
        app_certificate=secret(settings.agora_app_certificate),
        channel_name=channel,
        uid=agent_uid,
        token_expire=TOKEN_SECONDS,
    )


def user_token(settings: Settings, channel: str, user_uid: int) -> str:
    """RTC token for the browser: join the channel and publish the microphone."""
    return generate_rtc_token(
        app_id=settings.agora_app_id or "",
        app_certificate=secret(settings.agora_app_certificate),
        channel=channel,
        uid=user_uid,
        expiry_seconds=TOKEN_SECONDS,
    )


def llm_url(settings: Settings, session_id: uuid.UUID) -> str:
    return f"{(settings.public_base_url or '').rstrip('/')}/voice/llm/{session_id}/chat/completions"


def build_join_body(
    settings: Settings,
    *,
    session_id: uuid.UUID,
    channel: str,
    user_uid: int,
    agent_uid: int,
    token: str,
    first_name: str,
) -> dict[str, Any]:
    """The start-agent request: Agora's ASR, CoO as the LLM, Murf as the voice."""
    shared_key = secret(settings.custom_llm_api_key)
    return {
        "name": f"coo-{session_id.hex}",  # must be unique per agent
        "properties": {
            "channel": channel,
            "token": token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": [str(user_uid)],
            "idle_timeout": settings.agora_idle_timeout_seconds,
            "asr": {"language": settings.agora_asr_language, "vendor": "ares", "params": {}},
            "llm": {
                "url": llm_url(settings, session_id),
                "api_key": shared_key,
                "headers": {VOICE_KEY_HEADER: shared_key},
                "style": "openai",
                "system_messages": [],
                # CoO keeps the real history in Postgres; Agora only needs the latest words.
                "max_history": 4,
                "params": {"model": "coo"},
                "greeting_message": f"Hi {first_name}, I'm CoO. How can I help?",
                "failure_message": "Sorry, I couldn't get that. Please try again.",
            },
            "tts": {
                "vendor": "murf",
                "params": {
                    "api_key": secret(settings.murf_api_key),
                    "voiceId": settings.murf_voice_id,
                    "model": settings.murf_model,
                    "locale": settings.murf_locale,
                    "sample_rate": 24000,
                },
            },
        },
    }


class AgoraClient:
    """Agora's agent REST API. `transport` lets tests stand in for Agora."""

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _auth(self, settings: Settings, channel: str, agent_uid: int) -> str:
        customer_secret = secret(settings.agora_customer_secret)
        if settings.agora_customer_id and customer_secret:
            pair = f"{settings.agora_customer_id}:{customer_secret}".encode()
            return f"Basic {base64.b64encode(pair).decode()}"
        return f'agora token="{agent_token(settings, channel, agent_uid)}"'

    async def _post(self, settings: Settings, path: str, auth: str, body: dict | None) -> dict[str, Any]:
        url = f"{settings.agora_api_base.rstrip('/')}/projects/{settings.agora_app_id}{path}"
        async with httpx2.AsyncClient(timeout=15.0, transport=self._transport) as client:
            try:
                response = await client.post(url, json=body, headers={"Authorization": auth})
            except httpx2.HTTPError as exc:
                raise AgoraError(f"couldn't reach Agora ({type(exc).__name__})") from exc
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if response.status_code >= 400:
            reason = data.get("reason") or data.get("detail") or response.text[:200]
            raise AgoraError(f"Agora answered {response.status_code}: {reason}")
        return data

    async def start_agent(
        self, settings: Settings, body: dict[str, Any], *, channel: str, agent_uid: int
    ) -> str:
        """Start CoO's agent in the channel; returns Agora's agent id."""
        data = await self._post(settings, "/join", self._auth(settings, channel, agent_uid), body)
        agent_id = data.get("agent_id")
        if not agent_id:
            raise AgoraError("Agora didn't return an agent id")
        logger.info("agora agent started agent_id=%s status=%s", agent_id, data.get("status"))
        return str(agent_id)

    async def stop_agent(self, settings: Settings, agent_id: str, *, channel: str, agent_uid: int) -> None:
        await self._post(
            settings, f"/agents/{agent_id}/leave", self._auth(settings, channel, agent_uid), None
        )
        logger.info("agora agent stopped agent_id=%s", agent_id)
