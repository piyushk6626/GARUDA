"""Optional LangGraph adapter using the implemented tool and unchanged contracts."""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolContext
from garuda.optimization.assign_missions.schemas import AssignMissionsInput
from garuda.optimization.assign_missions.tool import assign_missions


class State(TypedDict, total=False):
    payload: dict[str, Any]
    ctx: ToolContext
    output: dict[str, Any]


async def run(state: State):
    output = await assign_missions(
        AssignMissionsInput.model_validate(state["payload"]), state["ctx"]
    )
    return {"output": output.model_dump(mode="json")}


def _graph():
    graph = StateGraph(State)
    graph.add_node("assign_missions", run)
    graph.set_entry_point("assign_missions")
    graph.add_edge("assign_missions", END)
    return graph.compile()


GRAPH = _graph()
