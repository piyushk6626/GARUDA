"""Linear LangGraph skeleton for one tool.

Requires the `api` extra (`langgraph`). Tool functions themselves do not import this
module, so the rest of the package still loads without it.

Each graph is `steps[0] -> ... -> steps[-1] -> END`. Middle nodes keep the payload.
The last node writes `output` to the fixed stub for that tool.
"""

from collections.abc import Sequence
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel


class ToolGraphState(TypedDict, total=False):
    payload: dict[str, Any]
    output: dict[str, Any]


def compile_tool_graph(steps: Sequence[str], output: BaseModel):
    """Compile a straight-line graph whose last node emits `output`."""
    if not steps:
        raise ValueError("a tool graph needs at least one step")
    body = output.model_dump(mode="json")
    graph = StateGraph(ToolGraphState)
    *middle, last = steps

    def keep(name: str):
        async def node(state: ToolGraphState) -> dict[str, Any]:
            return {"payload": state.get("payload", {})}

        node.__name__ = name
        return node

    async def emit(_: ToolGraphState) -> dict[str, Any]:
        return {"output": body}

    emit.__name__ = last
    for name in middle:
        graph.add_node(name, keep(name))
    graph.add_node(last, emit)
    graph.set_entry_point(steps[0])
    for left, right in zip(steps, steps[1:]):
        graph.add_edge(left, right)
    graph.add_edge(last, END)
    return graph.compile()
