from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_mission_success.schemas import (
    MissionSuccess,
    PredictMissionSuccessInput,
    PredictMissionSuccessOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="predict_mission_success",
    description="Probability a package achieves its effect and returns.",
    input_model=PredictMissionSuccessInput,
    output_model=PredictMissionSuccessOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = PredictMissionSuccessOutput(
    items=[
        MissionSuccess(
            mission_id="M-1001",
            p_success=0.72,
            p_return=0.9,
            ci_low=0.6,
            ci_high=0.84,
            drivers=["sead_support"],
        )
    ],
    plan_expected_value=1.0,
)


async def predict_mission_success(
    payload: PredictMissionSuccessInput, ctx: ToolContext
) -> PredictMissionSuccessOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_mission_success)
