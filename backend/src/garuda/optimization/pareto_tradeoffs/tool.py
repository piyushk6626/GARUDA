from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.pareto_tradeoffs.schemas import ParetoTradeoffsInput, ParetoTradeoffsOutput

SPEC = ToolSpec(
    name="pareto_tradeoffs",
    description="Pareto-optimal plan options across value, risk, munitions, fatigue, disruption.",
    input_model=ParetoTradeoffsInput,
    output_model=ParetoTradeoffsOutput,
    side_effects="none",
    timeout_s=120,
)


async def pareto_tradeoffs(payload: ParetoTradeoffsInput, ctx: ToolContext) -> ParetoTradeoffsOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, pareto_tradeoffs)
