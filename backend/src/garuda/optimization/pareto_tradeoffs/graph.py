"""Optional LangGraph adapter for the implemented tool."""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolContext
from garuda.optimization.pareto_tradeoffs.schemas import ParetoTradeoffsInput
from garuda.optimization.pareto_tradeoffs.tool import pareto_tradeoffs


class State(TypedDict, total=False):
    payload: dict[str, Any]
    ctx: ToolContext
    output: dict[str, Any]


async def run(state: State):
    result = await pareto_tradeoffs(
        ParetoTradeoffsInput.model_validate(state["payload"]), state["ctx"]
    )
    return {"output": result.model_dump(mode="json")}


def _graph():
    graph = StateGraph(State)
    graph.add_node("pareto_tradeoffs", run)
    graph.set_entry_point("pareto_tradeoffs")
    graph.add_edge("pareto_tradeoffs", END)
    return graph.compile()


GRAPH = _graph()
