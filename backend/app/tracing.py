"""Optional LangSmith tracing: watch every CoO turn step by step at https://smith.langchain.com.

Off unless LANGSMITH_TRACING=true and LANGSMITH_API_KEY are in .env. LangSmith reads its
settings from the process environment, and this app reads .env into Settings only, so
`configure` copies them across once at startup (before the first turn is traced).

What a trace shows: the turn (user, role, conversation), each model call (the system
prompt, the messages and the tools offered, then the reply and tool calls), and each tool
run (its input and result). Traces leave this machine: they hold whatever the turn held,
including email text, employee records and what the user said.
"""

import logging
import os

from app.config import Settings, secret

logger = logging.getLogger(__name__)


def configure(settings: Settings) -> bool:
    """Turn LangSmith tracing on when it's configured. Returns whether it's on."""
    key = secret(settings.langsmith_api_key)
    if not settings.langsmith_tracing:
        return False
    if not key:
        logger.warning("LANGSMITH_TRACING is true but LANGSMITH_API_KEY is empty: tracing stays off")
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    logger.info("LangSmith tracing on: project %s", settings.langsmith_project)
    return True
