from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_mission_success.schemas import PredictMissionSuccessInput, PredictMissionSuccessOutput

SPEC = ToolSpec(
    name="predict_mission_success",
    description="Probability a package achieves its effect and returns.",
    input_model=PredictMissionSuccessInput,
    output_model=PredictMissionSuccessOutput,
    side_effects="none",
    timeout_s=10,
)


async def predict_mission_success(payload: PredictMissionSuccessInput, ctx: ToolContext) -> PredictMissionSuccessOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_mission_success)
