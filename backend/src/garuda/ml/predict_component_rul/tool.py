from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_component_rul.schemas import PredictComponentRulInput, PredictComponentRulOutput

SPEC = ToolSpec(
    name="predict_component_rul",
    description="Component failure probability and remaining useful life.",
    input_model=PredictComponentRulInput,
    output_model=PredictComponentRulOutput,
    side_effects="none",
    timeout_s=20,
)


async def predict_component_rul(payload: PredictComponentRulInput, ctx: ToolContext) -> PredictComponentRulOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_component_rul)
