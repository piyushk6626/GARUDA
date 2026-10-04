"""Tools the simulation agent may call: two simulation tools, two query agents, ask, and finish."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.harness.retrieval.schemas import ToolCallRecord
from garuda.harness.retrieval.tools import (
    ASK_NAME,
    FINISH_NAME,
    AskQuestionInput,
    _schema_tool,
)
from garuda.harness.tasks.schemas import TaskFinishInput
from garuda.harness.tasks.session import TaskSession
from garuda.harness.tasks.tools import (
    ASK_ONTOLOGY,
    ASK_RETRIEVAL,
    SubAgent,
    SubAgentQueryInput,
    ToolFn,
    _run_main,
    _run_sub,
)
from garuda.sim.evaluate_plan_monte_carlo.tool import SPEC as MONTE_CARLO_SPEC
from garuda.sim.evaluate_plan_monte_carlo.tool import evaluate_plan_monte_carlo
from garuda.sim.rl_retask.tool import SPEC as RL_RETASK_SPEC
from garuda.sim.rl_retask.tool import rl_retask

SIM_TOOLS: dict[str, tuple[ToolSpec, ToolFn]] = {
    MONTE_CARLO_SPEC.name: (MONTE_CARLO_SPEC, evaluate_plan_monte_carlo),
    RL_RETASK_SPEC.name: (RL_RETASK_SPEC, rl_retask),
}
SIM_TOOL_NAMES = frozenset(SIM_TOOLS)
AGENT_TOOL_NAMES = frozenset({ASK_RETRIEVAL, ASK_ONTOLOGY})

MONTE_CARLO_DESCRIPTION = (
    "Run a plan many times under uncertainty (default 5000 runs). Optional stress: degraded_weather, "
    "heavy_threat, or tanker_loss. Returns the value distribution, mission success, expected losses, "
    "chance of beating the value threshold, CVaR at 5%, sensitivities, and weakest links."
)
RL_RETASK_DESCRIPTION = (
    "Stretch, advisory only. A reinforcement-learning policy retasks a plan after one event. Returns the "
    "new plan id, the diff, the policy version, decision latency, and an optional comparison with the "
    "MILP repair."
)


def model_tools() -> list[StructuredTool]:
    """The two simulation tools, ontology and retrieval, and the two control tools."""
    return [
        _schema_tool(MONTE_CARLO_SPEC.name, MONTE_CARLO_DESCRIPTION, MONTE_CARLO_SPEC.input_model),
        _schema_tool(RL_RETASK_SPEC.name, RL_RETASK_DESCRIPTION, RL_RETASK_SPEC.input_model),
        _schema_tool(
            ASK_ONTOLOGY,
            "Ask the ontology agent for relationships and ids, such as the sorties in a plan or missions "
            "for a target. One focused question.",
            SubAgentQueryInput,
        ),
        _schema_tool(
            ASK_RETRIEVAL,
            "Ask the retrieval agent for live data an event or stress case depends on: aircraft "
            "availability, threats, weather, tanker status, or mission requests. One focused question.",
            SubAgentQueryInput,
        ),
        _schema_tool(
            ASK_NAME,
            "Ask the main agent or a human one question when a required input is missing. Do not guess.",
            AskQuestionInput,
        ),
        _schema_tool(
            FINISH_NAME,
            "Hand this simulation back. status is done, partial, or blocked. "
            "assumptions lists choices you made. note is a short reason with no numbers.",
            TaskFinishInput,
        ),
    ]


def default_sub_agents() -> dict[str, SubAgent]:
    """Ontology and retrieval, keyed by the tool name the simulation model calls."""
    from garuda.harness.ontology.graph import query_graph
    from garuda.harness.retrieval.graph import retrieve

    return {ASK_RETRIEVAL: retrieve, ASK_ONTOLOGY: query_graph}


def make_run_call(
    session: TaskSession,
    sub_agents: dict[str, SubAgent],
) -> Callable[[str, dict[str, Any], ToolContext], Awaitable[ToolCallRecord]]:
    """Dispatch one model call to a simulation tool or to a query agent."""

    async def run_call(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolCallRecord:
        sim = SIM_TOOLS.get(name)
        if sim is not None:
            return await _run_main(sim[0], sim[1], args, ctx)
        agent = sub_agents.get(name) if name in AGENT_TOOL_NAMES else None
        if agent is None:
            return ToolCallRecord(name=name, args=args, ok=False, error=f"unknown tool: {name}")
        return await _run_sub(name, args, ctx, session, agent)

    return run_call
