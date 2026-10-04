"""LangGraph skeleton for `retask_rolling_horizon`.

Straight line: build_problem -> solve -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.optimization.retask_rolling_horizon.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("build_problem", "solve", "explain"), output=STUB_OUTPUT)
