from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.pareto_tradeoffs.schemas import ParetoTradeoffsInput, ParetoTradeoffsOutput, PlanOption
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="pareto_tradeoffs",
    description="Pareto-optimal plan options across value, risk, munitions, fatigue, disruption.",
    input_model=ParetoTradeoffsInput,
    output_model=ParetoTradeoffsOutput,
    side_effects="none",
    timeout_s=120,
)

STUB_OUTPUT = ParetoTradeoffsOutput(
    options=[
        PlanOption(
            plan_id="plan-stub",
            label="Balanced",
            objectives={"value": 1.0, "risk": 0.2},
            key_differences=["skeleton"],
        )
    ],
    ideal={"value": 1.0, "risk": 0.0},
    nadir={"value": 0.0, "risk": 1.0},
    recommendation="Balanced",
)


async def pareto_tradeoffs(payload: ParetoTradeoffsInput, ctx: ToolContext) -> ParetoTradeoffsOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, pareto_tradeoffs)
