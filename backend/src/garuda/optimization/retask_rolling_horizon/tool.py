from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.retask_rolling_horizon.schemas import RetaskRollingHorizonInput, RetaskRollingHorizonOutput

SPEC = ToolSpec(
    name="retask_rolling_horizon",
    description="Repair a plan after an event with minimal disruption.",
    input_model=RetaskRollingHorizonInput,
    output_model=RetaskRollingHorizonOutput,
    side_effects="draft",
    timeout_s=40,
)


async def retask_rolling_horizon(payload: RetaskRollingHorizonInput, ctx: ToolContext) -> RetaskRollingHorizonOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, retask_rolling_horizon)
