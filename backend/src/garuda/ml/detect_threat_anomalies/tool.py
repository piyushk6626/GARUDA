from garuda.contracts.geo import LatLon
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.detect_threat_anomalies.schemas import (
    AnomalyAlert,
    DetectThreatAnomaliesInput,
    DetectThreatAnomaliesOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="detect_threat_anomalies",
    description="Flag unusual threat or track activity.",
    input_model=DetectThreatAnomaliesInput,
    output_model=DetectThreatAnomaliesOutput,
    side_effects="none",
    timeout_s=20,
)

STUB_OUTPUT = DetectThreatAnomaliesOutput(
    alerts=[
        AnomalyAlert(
            id="A-001",
            type="new_cluster",
            location=LatLon(lat=27.1, lon=78.4),
            score=0.82,
            evidence=["isolation_forest"],
            suggested_threat_update="new_sam",
        )
    ],
    baseline_period="6h",
)


async def detect_threat_anomalies(
    payload: DetectThreatAnomaliesInput, ctx: ToolContext
) -> DetectThreatAnomaliesOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, detect_threat_anomalies)
