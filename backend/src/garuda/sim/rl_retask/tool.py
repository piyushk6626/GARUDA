from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.rl_retask.schemas import RlRetaskInput, RlRetaskOutput

SPEC = ToolSpec(
    name="rl_retask",
    description="(Stretch) RL policy retasking, advisory only.",
    input_model=RlRetaskInput,
    output_model=RlRetaskOutput,
    side_effects="none",
    timeout_s=20,
)


async def rl_retask(payload: RlRetaskInput, ctx: ToolContext) -> RlRetaskOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, rl_retask)
