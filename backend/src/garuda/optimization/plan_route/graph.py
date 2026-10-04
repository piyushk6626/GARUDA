"""Optional LangGraph adapter for the implemented route tool."""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolContext
from garuda.optimization.plan_route.schemas import PlanRouteInput
from garuda.optimization.plan_route.tool import plan_route


class State(TypedDict, total=False):
    payload: dict[str, Any]
    ctx: ToolContext
    output: dict[str, Any]


async def run(state: State):
    output = await plan_route(PlanRouteInput.model_validate(state["payload"]), state["ctx"])
    return {"output": output.model_dump(mode="json")}


def _graph():
    graph = StateGraph(State)
    graph.add_node("plan_route", run)
    graph.set_entry_point("plan_route")
    graph.add_edge("plan_route", END)
    return graph.compile()


GRAPH = _graph()
