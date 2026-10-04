"""LangGraph skeleton for `commit_plan`.

Straight line: check_token -> commit. `commit` always emits `STUB_OUTPUT`.
"""

from garuda.tools.adapters.langgraph import compile_tool_graph
from garuda.tools.commit.commit_plan import STUB_OUTPUT

GRAPH = compile_tool_graph(steps=("check_token", "commit"), output=STUB_OUTPUT)
