"""Typed tools, owned by specialists, run through one permission-checking executor.

A specialist is a named group of tools for one area (mail, HR, web...). The central
agent only sees the tools the current user may use, and the executor checks the
permission again before running anything, whatever the model asked for.
"""

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents.llm import ToolCall
from app.auth.permissions import Permission, permissions_for
from app.conversations import store
from app.models import PendingAction, Role, User

logger = logging.getLogger(__name__)

TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class UserContext:
    """Who the agent acts for. Built from the logged-in user, never from the model."""

    id: uuid.UUID
    name: str
    email: str
    role: Role
    permissions: frozenset[Permission]

    @classmethod
    def from_user(cls, user: User) -> "UserContext":
        return cls(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            permissions=permissions_for(user.role),
        )


@dataclass
class ToolContext:
    user: UserContext
    conversation_id: uuid.UUID
    session: AsyncSession
    proposals: list[PendingAction] = field(default_factory=list)

    async def propose(
        self, *, action_type: str, description: str, proposal: dict[str, Any]
    ) -> PendingAction:
        """Record a consequential action for approval. It does NOT perform the action."""
        action = await store.create_pending_action(
            self.session,
            self.user.id,
            self.conversation_id,
            action_type=action_type,
            description=description,
            proposal=proposal,
        )
        self.proposals.append(action)
        return action


class ToolError(Exception):
    """An expected failure the model should hear about (e.g. "no such contact")."""


@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]  # what the model sees
    card: dict[str, Any] | None = None  # optional structured block for the screen


Handler = Callable[[ToolContext, Any], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: Permission
    handler: Handler
    # True: the handler only records a proposal; nothing happens until the user approves.
    consequential: bool = False
    # True while the tool runs on sample data (the model is told, and the turn records it).
    mock: bool = True

    def definition(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


@dataclass(frozen=True)
class Specialist:
    name: str
    description: str
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str  # JSON text handed back to the model
    is_error: bool
    event: dict[str, Any]  # saved as a TOOL message for traceability
    card: dict[str, Any] | None = None


def proposal_card(action: PendingAction) -> dict[str, Any]:
    return {
        "type": "proposal",
        "action_id": str(action.id),
        "action_type": action.action_type,
        "description": action.description,
        "proposal": action.proposal,
        "status": action.status.value,
        "version": action.version,
    }


def awaiting_approval(action: PendingAction) -> ToolOutput:
    """What a consequential tool returns: a proposal, explicitly not a done deal."""
    return ToolOutput(
        data={
            "status": "awaiting_approval",
            "action_id": str(action.id),
            "will_do": action.description,
            "details": action.proposal,
            "note": "Nothing has happened yet. Tell the user exactly what will happen "
            "and ask them to approve it or say what to change.",
        },
        card=proposal_card(action),
    )


def _json_safe(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data, default=str))


class ToolRegistry:
    def __init__(self, specialists: Iterable[Specialist]) -> None:
        self._tools: dict[str, tuple[Specialist, ToolSpec]] = {}
        for specialist in specialists:
            for tool in specialist.tools:
                if not TOOL_NAME_PATTERN.match(tool.name):
                    raise ValueError(f"invalid tool name: {tool.name!r}")
                if tool.name in self._tools:
                    raise ValueError(f"duplicate tool name: {tool.name!r}")
                self._tools[tool.name] = (specialist, tool)

    def tool(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def definitions_for(self, user: UserContext) -> list[dict[str, Any]]:
        """Only the tools this user may use: the model never sees the others."""
        return [
            tool.definition()
            for _, tool in self._tools.values()
            if tool.permission in user.permissions
        ]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        entry = self._tools.get(call.name)
        if entry is None:
            return self._error(call, None, None, f"Unknown tool: {call.name}")
        specialist, tool = entry
        # Checked again here, whatever the model asked for.
        if tool.permission not in ctx.user.permissions:
            logger.warning(
                "tool refused user_id=%s tool=%s missing=%s", ctx.user.id, tool.name, tool.permission
            )
            return self._error(
                call, specialist, tool, "Permission denied: this user is not allowed to do that."
            )
        try:
            arguments = tool.input_model.model_validate(call.input)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or 'input'}: {err['msg']}"
                for err in exc.errors()
            )
            return self._error(call, specialist, tool, f"Invalid input: {problems}")
        try:
            output = await tool.handler(ctx, arguments)
        except ToolError as exc:
            return self._error(call, specialist, tool, str(exc))
        except Exception:
            logger.exception("tool crashed tool=%s", tool.name)
            return self._error(call, specialist, tool, "The tool failed unexpectedly.")

        data = _json_safe({**output.data, **({"mock": True} if tool.mock else {})})
        event = {
            "tool": tool.name,
            "specialist": specialist.name,
            "input": _json_safe(call.input),
            "ok": True,
            "mock": tool.mock,
            "consequential": tool.consequential,
            "output": data,
        }
        card = _json_safe(output.card) if output.card else None
        return ToolResult(
            call_id=call.id, content=json.dumps(data), is_error=False, event=event, card=card
        )

    @staticmethod
    def _error(
        call: ToolCall, specialist: Specialist | None, tool: ToolSpec | None, message: str
    ) -> ToolResult:
        data = {"error": message}
        event = {
            "tool": call.name,
            "specialist": specialist.name if specialist else None,
            "input": _json_safe(call.input),
            "ok": False,
            "mock": tool.mock if tool else False,
            "consequential": tool.consequential if tool else False,
            "output": data,
        }
        return ToolResult(call_id=call.id, content=json.dumps(data), is_error=True, event=event)
