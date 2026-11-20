import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.llm import ChatModel
from app.agents.specialists import build_registry
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.turns import router as turns_router
from app.config import get_settings
from app.conversations.store import ConversationNotFound
from app.db import Database


def create_app(database: Database | None = None, chat_model: ChatModel | None = None) -> FastAPI:
    """Build the FastAPI app. Tests pass their own `database` and `chat_model`."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = database or Database(settings.database_url, settings.db_connect_timeout_seconds)
        app.state.db = db
        try:
            yield
        finally:
            await db.dispose()

    app = FastAPI(title="CoO API", version="0.1.0", lifespan=lifespan)
    # Tokens travel in the Authorization header, not cookies, so credentials stay off.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The model is built on the first turn (see api/turns.py), so the app starts even
    # before AI credentials are set up.
    app.state.chat_model = chat_model
    app.state.tool_registry = build_registry()

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(turns_router)

    @app.exception_handler(ConversationNotFound)
    async def _conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
        # Same answer whether it doesn't exist or belongs to someone else.
        return JSONResponse(status_code=404, content={"detail": "Conversation not found"})

    return app


app = create_app()
