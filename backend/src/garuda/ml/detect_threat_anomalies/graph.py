"""LangGraph skeleton for `detect_threat_anomalies`.

Straight line: features -> predict -> explain. The last node always emits `STUB_OUTPUT`.
"""

from garuda.ml.detect_threat_anomalies.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("features", "predict", "explain"), output=STUB_OUTPUT)
