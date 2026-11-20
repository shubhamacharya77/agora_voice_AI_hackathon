"""A stand-in chat model that replays scripted turns, so agent tests need no AWS."""

import copy
import itertools
from typing import Any

from app.agents.llm import ModelTurn, ToolCall

_ids = itertools.count(1)


class ScriptedModel:
    name = "scripted"

    def __init__(self, *turns: ModelTurn | Exception) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def respond(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        self.calls.append(
            {"system": system, "messages": copy.deepcopy(messages), "tools": [t["name"] for t in tools]}
        )
        if not self._turns:
            raise AssertionError("the model was called more times than the test scripted")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def say(text: str) -> ModelTurn:
    """A final answer with no tool calls."""
    return ModelTurn(
        text=text, tool_calls=[], stop_reason="end_turn", content=[{"type": "text", "text": text}]
    )


def use(*calls: tuple[str, dict[str, Any]], text: str = "") -> ModelTurn:
    """A model turn that asks for one or more tools."""
    tool_calls = [ToolCall(id=f"toolu_{next(_ids)}", name=name, input=args) for name, args in calls]
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls]
    return ModelTurn(text=text, tool_calls=tool_calls, stop_reason="tool_use", content=content)
