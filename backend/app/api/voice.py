"""Voice calls through Agora's Conversational AI Engine, with Murf as CoO's voice.

The browser starts a call (POST /voice/sessions) and joins the Agora channel. Agora's agent
hears the user, then calls POST /voice/llm/{session}/chat/completions here. That runs the
same turn as typed text (approvals, recall, switching conversations), and the reply
streams back for Murf to speak. The call maps to the user through the session id in the
URL and the shared secret header, never through anything Agora or the model says.
"""

import asyncio
import hmac
import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents.summaries import summarize_if_due
from app.agents.turn import merge_user_text, run_turn
from app.api.conversations import ChatUser
from app.api.turns import chat_model_for
from app.config import Settings, secret
from app.conversations import store
from app.deps import SessionDep, SettingsDep
from app.models import User, VoiceSession, VoiceStatus
from app.models.base import utcnow
from app.voice.agora import VOICE_KEY_HEADER, AgoraClient, AgoraError, agent_token, build_join_body, user_token
from app.voice.bridge import last_user_text, sse_reply, sse_turn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

AGENT_UID = 999_001
NOT_HEARD_REPLY = "Sorry, I didn't catch that."
# Said straight away when a turn takes longer than this (tool calls, a rate-limit wait).
FILLER = "One moment."
FILLER_AFTER_SECONDS = 1.2
# Agora often sends one sentence in growing pieces a fraction of a second apart. Wait this
# long for more before starting a turn, so the first fragment doesn't get its own answer.
SETTLE_SECONDS = 0.5


class _CallQueue:
    """Keeps one call's turns in order. Agora calls us each time the user speaks, often
    several times a second for one growing sentence. Turns run one at a time; speech that
    arrives while a turn runs waits, and when several pieces are waiting only the newest
    runs, with the others merged in. A running turn is never cancelled."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.latest = 0
        self.waiting: list[str] = []

    def add(self, text: str) -> int:
        self.latest += 1
        self.waiting.append(text)
        return self.latest

    def take(self, ticket: int) -> str | None:
        """The text to run for this ticket, or None if a newer ticket will run it."""
        if ticket != self.latest:
            return None
        combined = ""
        for piece in self.waiting:
            combined = merge_user_text(combined, piece)
        self.waiting = []
        return combined


_QUEUES: dict[uuid.UUID, _CallQueue] = {}


def _queue(session_id: uuid.UUID) -> _CallQueue:
    return _QUEUES.setdefault(session_id, _CallQueue())


class VoiceConfig(BaseModel):
    mode: Literal["agora", "browser"]  # browser = the stand-in until Agora is set up
    missing: list[str]  # .env keys still needed for Agora voice


class VoiceStart(BaseModel):
    conversation_id: uuid.UUID


class VoiceJoin(BaseModel):
    """What the browser needs to join the call."""

    session_id: uuid.UUID
    app_id: str
    channel: str
    uid: int
    token: str
    agent_uid: int
    conversation_id: uuid.UUID


class VoiceState(BaseModel):
    session_id: uuid.UUID
    status: VoiceStatus
    conversation_id: uuid.UUID  # follows "resume" and "new conversation" during the call
    error: str | None


def _agora(request: Request) -> AgoraClient:
    return request.app.state.agora


def _state(voice: VoiceSession) -> VoiceState:
    return VoiceState(
        session_id=voice.id, status=voice.status, conversation_id=voice.conversation_id, error=voice.error
    )


async def _owned(session: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> VoiceSession:
    voice = await session.get(VoiceSession, session_id, populate_existing=True)
    if voice is None or voice.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice session not found")
    return voice


async def _end(session: AsyncSession, voice: VoiceSession, settings: Settings, agora: AgoraClient) -> None:
    if voice.status is VoiceStatus.ACTIVE and voice.agent_id:
        try:
            await agora.stop_agent(settings, voice.agent_id, channel=voice.channel, agent_uid=voice.agent_uid)
        except AgoraError:
            logger.warning("couldn't stop agora agent %s; it ends on its idle timeout", voice.agent_id)
    voice.status = VoiceStatus.ENDED
    voice.ended_at = utcnow()
    session.add(voice)


@router.get("/config", response_model=VoiceConfig)
async def voice_config(user: ChatUser, settings: SettingsDep) -> VoiceConfig:
    missing = settings.voice_missing
    return VoiceConfig(mode="browser" if missing else "agora", missing=missing)


@router.post("/sessions", response_model=VoiceJoin)
async def start_voice(
    body: VoiceStart, request: Request, user: ChatUser, session: SessionDep, settings: SettingsDep
) -> VoiceJoin:
    missing = settings.voice_missing
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Voice isn't set up yet. Missing in .env: {', '.join(missing)}",
        )
    agora = _agora(request)
    conversation = await store.require_conversation(session, user.id, body.conversation_id)
    user_id, first_name = user.id, user.name.split(" ")[0]

    # One call at a time per user: end any call still open.
    open_calls = await session.exec(
        select(VoiceSession).where(VoiceSession.user_id == user_id, VoiceSession.status == VoiceStatus.ACTIVE)
    )
    for call in open_calls.all():
        await _end(session, call, settings, agora)

    voice = VoiceSession(
        user_id=user_id,
        conversation_id=conversation.id,
        channel="",
        user_uid=secrets.randbelow(800_000) + 100_000,
        agent_uid=AGENT_UID,
    )
    voice.channel = f"coo-{voice.id.hex[:20]}"
    session.add(voice)
    await session.commit()

    try:
        tokens = (
            agent_token(settings, voice.channel, voice.agent_uid),
            user_token(settings, voice.channel, voice.user_uid),
        )
    except Exception as exc:  # e.g. an App ID or certificate of the wrong length
        voice.status, voice.error = VoiceStatus.FAILED, f"Invalid Agora credentials ({exc})"
        session.add(voice)
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=voice.error) from None
    join = build_join_body(
        settings,
        session_id=voice.id,
        channel=voice.channel,
        user_uid=voice.user_uid,
        agent_uid=voice.agent_uid,
        token=tokens[0],
        first_name=first_name,
    )
    try:
        voice.agent_id = await agora.start_agent(settings, join, channel=voice.channel, agent_uid=voice.agent_uid)
    except AgoraError as exc:
        voice.status, voice.error = VoiceStatus.FAILED, str(exc)
        session.add(voice)
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Couldn't start the voice agent: {exc}") from None
    voice.status = VoiceStatus.ACTIVE
    session.add(voice)
    await session.commit()
    return VoiceJoin(
        session_id=voice.id,
        app_id=settings.agora_app_id or "",
        channel=voice.channel,
        uid=voice.user_uid,
        token=tokens[1],
        agent_uid=voice.agent_uid,
        conversation_id=voice.conversation_id,
    )


@router.get("/sessions/{session_id}", response_model=VoiceState)
async def voice_state(session_id: uuid.UUID, user: ChatUser, session: SessionDep) -> VoiceState:
    return _state(await _owned(session, user.id, session_id))


@router.delete("/sessions/{session_id}", response_model=VoiceState)
async def stop_voice(
    session_id: uuid.UUID, request: Request, user: ChatUser, session: SessionDep, settings: SettingsDep
) -> VoiceState:
    voice = await _owned(session, user.id, session_id)
    await _end(session, voice, settings, _agora(request))
    await session.commit()
    _QUEUES.pop(session_id, None)
    return _state(voice)


def _presented_key(request: Request) -> str:
    key = request.headers.get(VOICE_KEY_HEADER, "")
    if not key:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        key = value if scheme.lower() == "bearer" else ""
    return key


@router.post("/llm/{session_id}/chat/completions")
async def agora_llm(
    session_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Called by Agora's cloud (OpenAI chat-completions format) each time the user speaks."""
    expected, presented = secret(settings.custom_llm_api_key), _presented_key(request)
    if not expected or not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorized")
    voice = await session.get(VoiceSession, session_id, populate_existing=True)
    if voice is None or voice.status is not VoiceStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such voice session")
    user = await session.get(User, voice.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such voice session")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected JSON") from None

    text = last_user_text(payload.get("messages", []))
    if not text:
        return StreamingResponse(sse_reply(NOT_HEARD_REPLY), media_type="text/event-stream")

    voice_id, user_id, first_conversation = voice.id, user.id, voice.conversation_id
    model = chat_model_for(request, settings)
    app = request.app
    queue = _queue(voice_id)
    ticket = queue.add(text)

    async def turn() -> str | None:
        await asyncio.sleep(SETTLE_SECONDS)
        if ticket != queue.latest:
            return None  # more speech arrived: the newest piece runs, with this one merged in
        async with queue.lock:
            combined = queue.take(ticket)
            if combined is None:
                return None  # folded into a newer piece of speech
            # Its own session: the reply may still be streaming after this request returns.
            async with app.state.db.sessionmaker() as turn_session:
                # The call may have moved to another conversation during an earlier turn.
                current = await turn_session.get(VoiceSession, voice_id)
                conversation_id = current.conversation_id if current else first_conversation
                return await _run_voice_turn(turn_session, combined, conversation_id)

    async def _run_voice_turn(turn_session: AsyncSession, said: str, conversation_id: uuid.UUID) -> str:
        speaker = await turn_session.get(User, user_id)
        result = await run_turn(
            turn_session,
            speaker,
            conversation_id,
            said,
            model=model,
            registry=app.state.tool_registry,
            settings=settings,
            graph=app.state.turn_graph,
            source="voice",
        )
        if result.switch_to is not None:  # the call follows "resume" or "new conversation"
            await turn_session.exec(
                update(VoiceSession)
                .where(col(VoiceSession.id) == voice_id)
                .values(conversation_id=result.switch_to)
                .execution_options(synchronize_session=False)
            )
            await turn_session.commit()
        return result.reply

    task = asyncio.create_task(turn())
    background.add_task(_summarize_after, task, app, user_id, voice_id, model, settings)
    return StreamingResponse(
        sse_turn(task, filler=FILLER, filler_after=FILLER_AFTER_SECONDS),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _summarize_after(task: "asyncio.Task[str | None]", app, user_id, voice_id, model, settings: Settings) -> None:
    try:
        # Shielded: if this request is dropped, the turn must still finish.
        if await asyncio.shield(task) is None:
            return  # folded into a newer turn, which summarizes
    except Exception:
        return
    async with app.state.db.sessionmaker() as session:
        voice = await session.get(VoiceSession, voice_id)
        conversation_id = voice.conversation_id if voice else None
    if conversation_id is not None:
        await summarize_if_due(app.state.db, user_id, conversation_id, model, settings.summary_every_messages)
