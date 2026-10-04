"""LangGraph skeleton for `assess_crew_fatigue`.

Straight line: features -> predict -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.ml.assess_crew_fatigue.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("features", "predict", "explain"), output=STUB_OUTPUT)
