from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.schedule_crew.schemas import ScheduleCrewInput, ScheduleCrewOutput

SPEC = ToolSpec(
    name="schedule_crew",
    description="Assign qualified, rested crew to the sortie slots of a plan.",
    input_model=ScheduleCrewInput,
    output_model=ScheduleCrewOutput,
    side_effects="draft",
    timeout_s=40,
)


async def schedule_crew(payload: ScheduleCrewInput, ctx: ToolContext) -> ScheduleCrewOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_crew)
