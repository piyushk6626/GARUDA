"""LangGraph skeleton for `predict_mission_success`.

Straight line: features -> predict -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.ml.predict_mission_success.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("features", "predict", "explain"), output=STUB_OUTPUT)
