"""Optional LangGraph adapter to the same public advisory tool."""

from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from garuda.contracts.tool import ToolInputError
from garuda.sim.rl_retask.schemas import RlRetaskInput
from garuda.sim.rl_retask.tool import rl_retask


class State(TypedDict, total=False):
    payload: dict[str, Any]
    output: dict[str, Any]


async def decide(state: State, config: RunnableConfig):
    ctx = config.get("configurable", {}).get("ctx")
    if ctx is None:
        raise ToolInputError(
            "RL retasking graph requires config.configurable.ctx (ToolContext)"
        )
    output = await rl_retask(RlRetaskInput.model_validate(state["payload"]), ctx)
    return {"output": output.model_dump(mode="json")}


_graph = StateGraph(State)
_graph.add_node("observe_act_repair", decide)
_graph.set_entry_point("observe_act_repair")
_graph.add_edge("observe_act_repair", END)
GRAPH = _graph.compile()
