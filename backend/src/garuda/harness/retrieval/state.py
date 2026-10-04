"""Graph state for one retrieval run."""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def extend_calls(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*left, *right]


class RetrievalState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    calls: Annotated[list[dict[str, Any]], extend_calls]
    tool_budget: int
    finished: bool
    question: str
    finish_args: dict[str, Any] | None
