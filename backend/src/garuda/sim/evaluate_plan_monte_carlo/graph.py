"""LangGraph skeleton for `evaluate_plan_monte_carlo`.

Straight line: sample -> simulate -> aggregate. The last node always emits `STUB_OUTPUT`.
"""

from garuda.sim.evaluate_plan_monte_carlo.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("sample", "simulate", "aggregate"), output=STUB_OUTPUT)
