from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.schedule_tanker_aar.schemas import ScheduleTankerAarInput, ScheduleTankerAarOutput

SPEC = ToolSpec(
    name="schedule_tanker_aar",
    description="Schedule tankers to meet receivers at air refuelling points.",
    input_model=ScheduleTankerAarInput,
    output_model=ScheduleTankerAarOutput,
    side_effects="draft",
    timeout_s=30,
)


async def schedule_tanker_aar(payload: ScheduleTankerAarInput, ctx: ToolContext) -> ScheduleTankerAarOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_tanker_aar)
