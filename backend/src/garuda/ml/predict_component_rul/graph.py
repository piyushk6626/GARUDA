"""LangGraph skeleton for `predict_component_rul`.

Straight line: features -> predict -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.ml.predict_component_rul.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("features", "predict", "explain"), output=STUB_OUTPUT)
