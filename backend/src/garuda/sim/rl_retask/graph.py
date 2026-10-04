"""LangGraph skeleton for `rl_retask`.

Straight line: observe -> act -> repair. The last node always emits `STUB_OUTPUT`.
"""

from garuda.sim.rl_retask.tool import STUB_OUTPUT
from garuda.tools.adapters.langgraph import compile_tool_graph

GRAPH = compile_tool_graph(steps=("observe", "act", "repair"), output=STUB_OUTPUT)
