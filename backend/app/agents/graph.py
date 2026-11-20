"""The central CoO agent as a LangGraph graph.

    START -> central_agent --tool calls?--> specialists -> central_agent ... -> END

`central_agent` asks the model what to do. `specialists` runs the requested tools through
the permission-checking registry and hands the results back. The loop ends when the
model answers without tools, or after `max_steps` model calls.

Per-turn dependencies (model, registry, the user's identity) travel in LangGraph's
runtime context, not in the state, so nothing the model produces can replace them.
"""

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agents.llm import ChatModel, ToolCall
from app.agents.tools import ToolContext, ToolRegistry

STEP_LIMIT_REPLY = (
    "That needed more steps than I can take in one go. Could you split it into smaller requests?"
)


@dataclass
class AgentContext:
    model: ChatModel
    registry: ToolRegistry
    tool_context: ToolContext
    system: str
    tools: list[dict[str, Any]]
    max_steps: int


class TurnState(TypedDict):
    messages: list[dict[str, Any]]  # what the model sees this turn
    tool_calls: list[dict[str, Any]]  # requested by the last model call, not yet run
    steps: int  # model calls so far
    reply: str
    cards: list[dict[str, Any]]  # structured results for the screen
    events: list[dict[str, Any]]  # every tool run, for traceability


async def central_agent(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    turn = await ctx.model.respond(system=ctx.system, messages=state["messages"], tools=ctx.tools)
    steps = state["steps"] + 1
    update: dict[str, Any] = {
        "messages": [*state["messages"], {"role": "assistant", "content": turn.content}],
        "steps": steps,
        "reply": turn.text,
        "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in turn.tool_calls],
    }
    if turn.tool_calls and steps >= ctx.max_steps:
        update["tool_calls"] = []
        update["reply"] = STEP_LIMIT_REPLY
    return update


async def specialists(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    results = []
    for call in state["tool_calls"]:  # one at a time: tools share one database session
        results.append(await ctx.registry.execute(ToolCall(**call), ctx.tool_context))
    tool_results = [
        {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
        for r in results
    ]
    return {
        # Every result goes back in ONE user message.
        "messages": [*state["messages"], {"role": "user", "content": tool_results}],
        "tool_calls": [],
        "events": [*state["events"], *(r.event for r in results)],
        "cards": [*state["cards"], *(r.card for r in results if r.card)],
    }


def _route(state: TurnState) -> str:
    return "specialists" if state["tool_calls"] else END


def build_turn_graph():
    graph = StateGraph(TurnState, context_schema=AgentContext)
    graph.add_node("central_agent", central_agent)
    graph.add_node("specialists", specialists)
    graph.add_edge(START, "central_agent")
    graph.add_conditional_edges("central_agent", _route, ["specialists", END])
    graph.add_edge("specialists", "central_agent")
    return graph.compile()


TURN_GRAPH = build_turn_graph()
