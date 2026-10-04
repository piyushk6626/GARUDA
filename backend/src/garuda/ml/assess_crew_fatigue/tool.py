from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.assess_crew_fatigue.schemas import AssessCrewFatigueInput, AssessCrewFatigueOutput, CrewFatigue
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="assess_crew_fatigue",
    description="Crew fatigue now and under a proposed plan.",
    input_model=AssessCrewFatigueInput,
    output_model=AssessCrewFatigueOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = AssessCrewFatigueOutput(
    items=[
        CrewFatigue(
            crew_id="C-001",
            now=0.3,
            projected_peak=0.45,
            risk_band="AMBER",
            drivers=["duty_hours"],
        )
    ],
    base_summary={"B1": {"GREEN": 4, "AMBER": 1, "RED": 0}},
)


async def assess_crew_fatigue(payload: AssessCrewFatigueInput, ctx: ToolContext) -> AssessCrewFatigueOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assess_crew_fatigue)
