"""LangGraph skeleton for `deconflict_airspace`.

Straight line: build_problem -> solve -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.optimization.deconflict_airspace.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("build_problem", "solve", "explain"), output=STUB_OUTPUT)
