import asyncio

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.pareto_tradeoffs.explain import explain
from garuda.optimization.pareto_tradeoffs.model import solve
from garuda.optimization.pareto_tradeoffs.problem import build_problem
from garuda.optimization.pareto_tradeoffs.schemas import (
    ParetoTradeoffsInput,
    ParetoTradeoffsOutput,
)

SPEC = ToolSpec(
    name="pareto_tradeoffs",
    description="Pareto-optimal plan options across value, risk, munitions, fatigue, disruption.",
    input_model=ParetoTradeoffsInput,
    output_model=ParetoTradeoffsOutput,
    side_effects="none",
    timeout_s=120,
)


async def pareto_tradeoffs(
    payload: ParetoTradeoffsInput, ctx: ToolContext
) -> ParetoTradeoffsOutput:
    """Compute validated alternatives using the unchanged input/output contract."""

    # The shared run_cpu executor is still a stub. Keep CPU work off the loop.
    def compute():
        problem = build_problem(payload, ctx)
        return explain(problem, solve(problem))

    return await asyncio.to_thread(compute)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, pareto_tradeoffs)
