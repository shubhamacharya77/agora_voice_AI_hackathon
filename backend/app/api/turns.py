"""Talk to CoO: one turn in, one reply out.

Text in for now; in Phase 7, Agora's voice engine calls the same `run_turn`.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.agents.llm import ChatModel, build_chat_model
from app.agents.tools import ToolRegistry
from app.agents.turn import run_turn
from app.api.conversations import ChatUser, MessagePublic, PendingActionPublic
from app.config import Settings
from app.deps import SessionDep, SettingsDep

router = APIRouter(prefix="/conversations", tags=["agent"])


class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TurnResponse(BaseModel):
    reply: str
    cards: list[dict[str, Any]]
    pending_actions: list[PendingActionPublic]
    error: bool
    user_message: MessagePublic
    assistant_message: MessagePublic


def _chat_model(request: Request, settings: Settings) -> ChatModel:
    if request.app.state.chat_model is None:  # built on first use
        request.app.state.chat_model = build_chat_model(settings)
    return request.app.state.chat_model


@router.post("/{conversation_id}/turns", response_model=TurnResponse)
async def take_turn(
    conversation_id: uuid.UUID,
    body: TurnRequest,
    request: Request,
    user: ChatUser,
    session: SessionDep,
    settings: SettingsDep,
) -> TurnResponse:
    registry: ToolRegistry = request.app.state.tool_registry
    result = await run_turn(
        session,
        user,
        conversation_id,
        body.text.strip(),
        model=_chat_model(request, settings),
        registry=registry,
        settings=settings,
    )
    return TurnResponse(
        reply=result.reply,
        cards=result.cards,
        pending_actions=[PendingActionPublic.model_validate(a) for a in result.pending_actions],
        error=result.error,
        user_message=MessagePublic.model_validate(result.user_message),
        assistant_message=MessagePublic.model_validate(result.assistant_message),
    )
