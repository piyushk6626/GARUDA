from garuda.contracts.plan import PlanDiff
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.retask_rolling_horizon.schemas import (
    RetaskRollingHorizonInput,
    RetaskRollingHorizonOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="retask_rolling_horizon",
    description="Repair a plan after an event with minimal disruption.",
    input_model=RetaskRollingHorizonInput,
    output_model=RetaskRollingHorizonOutput,
    side_effects="draft",
    timeout_s=40,
)

STUB_OUTPUT = RetaskRollingHorizonOutput(
    new_plan_id="plan-stub",
    diff=PlanDiff(),
    missions_degraded=["M-1002"],
    kpi_delta={"value": 0.0, "risk": 0.0},
    status="FEASIBLE",
)


async def retask_rolling_horizon(
    payload: RetaskRollingHorizonInput, ctx: ToolContext
) -> RetaskRollingHorizonOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, retask_rolling_horizon)
