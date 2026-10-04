"""Optional LangGraph adapter. Supply ToolContext in config.configurable.ctx."""

import asyncio
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolInputError
from garuda.optimization.retask_rolling_horizon.model import solve
from garuda.optimization.retask_rolling_horizon.problem import build_problem
from garuda.optimization.retask_rolling_horizon.schemas import RetaskRollingHorizonInput
from garuda.optimization.retask_rolling_horizon.tool import finish


class State(TypedDict, total=False):
    payload: dict[str, Any]
    problem: Any
    solution: Any
    output: dict[str, Any]


def _context(config):
    ctx = config.get("configurable", {}).get("ctx")
    if ctx is None:
        raise ToolInputError(
            "Retasking graph requires config.configurable.ctx (ToolContext)"
        )
    return ctx


async def gather(state: State, config: RunnableConfig):
    payload = RetaskRollingHorizonInput.model_validate(state["payload"])
    problem = await asyncio.to_thread(build_problem, payload, _context(config))
    return {"problem": problem}


async def optimise(state: State):
    return {"solution": await asyncio.to_thread(solve, state["problem"])}


async def emit(state: State, config: RunnableConfig):
    payload = RetaskRollingHorizonInput.model_validate(state["payload"])
    output = await asyncio.to_thread(
        finish, payload, _context(config), state["problem"], state["solution"]
    )
    return {"output": output.model_dump(mode="json")}


_graph = StateGraph(State)
_graph.add_node("build_problem", gather)
_graph.add_node("solve", optimise)
_graph.add_node("explain", emit)
_graph.set_entry_point("build_problem")
_graph.add_edge("build_problem", "solve")
_graph.add_edge("solve", "explain")
_graph.add_edge("explain", END)
GRAPH = _graph.compile()
