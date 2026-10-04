from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.detect_threat_anomalies.schemas import DetectThreatAnomaliesInput, DetectThreatAnomaliesOutput

SPEC = ToolSpec(
    name="detect_threat_anomalies",
    description="Flag unusual threat or track activity.",
    input_model=DetectThreatAnomaliesInput,
    output_model=DetectThreatAnomaliesOutput,
    side_effects="none",
    timeout_s=20,
)


async def detect_threat_anomalies(payload: DetectThreatAnomaliesInput, ctx: ToolContext) -> DetectThreatAnomaliesOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, detect_threat_anomalies)
