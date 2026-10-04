from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.assess_crew_fatigue.schemas import AssessCrewFatigueInput, AssessCrewFatigueOutput

SPEC = ToolSpec(
    name="assess_crew_fatigue",
    description="Crew fatigue now and under a proposed plan.",
    input_model=AssessCrewFatigueInput,
    output_model=AssessCrewFatigueOutput,
    side_effects="none",
    timeout_s=10,
)


async def assess_crew_fatigue(payload: AssessCrewFatigueInput, ctx: ToolContext) -> AssessCrewFatigueOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assess_crew_fatigue)
