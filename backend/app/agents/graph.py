"""The central CoO agent as a LangGraph graph.

    START -> central_agent --tool calls?--> specialists -> central_agent ... -> END

`central_agent` asks the model what to do. `specialists` runs the requested tools through
the permission-checking registry and hands the results back. The loop ends when the
model answers without tools, or after `max_steps` model calls.

Per-turn dependencies (model, registry, the user's identity) travel in LangGraph's
runtime context, not in the state, so nothing the model produces can replace them.

With a checkpointer (the app uses LangGraph's Postgres saver), every step of every turn is
checkpointed under the conversation's id as the thread id. The state holds only plain
JSON, so it serializes safely. Checkpoints record the agent's progress; they are never
treated as chat messages, which live in the messages table.
"""

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import traceable

from app.agents.llm import ChatModel, ModelTurn, ToolCall
from app.agents.tools import ToolContext, ToolRegistry, ToolResult

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


# LangSmith steps (no-ops unless tracing is on; see app/tracing.py): each model call and
# each tool run shows up as its own step, with what went in and what came out.


@traceable(
    run_type="llm",
    name="model",
    process_inputs=lambda i: {"system": i["system"], "messages": i["messages"], "tools": [t["name"] for t in i["tools"]]},
    process_outputs=lambda t: {"text": t.text, "tool_calls": [{"name": c.name, "input": c.input} for c in t.tool_calls]},
)
async def _ask_model(model: ChatModel, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
    return await model.respond(system=system, messages=messages, tools=tools)


@traceable(
    run_type="tool",
    process_inputs=lambda i: {"input": i["call"].input},
    process_outputs=lambda r: {"content": r.content, "is_error": r.is_error},
)
async def _run_tool(registry: ToolRegistry, call: ToolCall, context: ToolContext) -> ToolResult:
    return await registry.execute(call, context)


async def central_agent(state: TurnState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    turn = await _ask_model(ctx.model, ctx.system, state["messages"], ctx.tools)
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
        tool_call = ToolCall(**call)
        results.append(await _run_tool(ctx.registry, tool_call, ctx.tool_context, langsmith_extra={"name": tool_call.name}))
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


def build_turn_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(TurnState, context_schema=AgentContext)
    graph.add_node("central_agent", central_agent)
    graph.add_node("specialists", specialists)
    graph.add_edge(START, "central_agent")
    graph.add_conditional_edges("central_agent", _route, ["specialists", END])
    graph.add_edge("specialists", "central_agent")
    return graph.compile(checkpointer=checkpointer)


# Without checkpoints: used when the Postgres saver isn't available, and in unit tests.
TURN_GRAPH = build_turn_graph()
