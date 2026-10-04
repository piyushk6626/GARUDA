from garuda.contracts.plan import PlanDiff
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.rl_retask.schemas import RlRetaskInput, RlRetaskOutput
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="rl_retask",
    description="(Stretch) RL policy retasking, advisory only.",
    input_model=RlRetaskInput,
    output_model=RlRetaskOutput,
    side_effects="none",
    timeout_s=20,
)

STUB_OUTPUT = RlRetaskOutput(
    new_plan_id="plan-stub",
    diff=PlanDiff(),
    policy_version="skeleton-0",
    decision_latency_ms=12.0,
    compare_with_milp={"value_gap": 0.0},
)


async def rl_retask(payload: RlRetaskInput, ctx: ToolContext) -> RlRetaskOutput:
    """Skeleton: same output for every input until the policy is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, rl_retask)
