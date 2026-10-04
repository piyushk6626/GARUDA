from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.ml.predict_component_rul.schemas import (
    ComponentRisk,
    MaintenanceAdvice,
    PredictComponentRulInput,
    PredictComponentRulOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="predict_component_rul",
    description="Component failure probability and remaining useful life.",
    input_model=PredictComponentRulInput,
    output_model=PredictComponentRulOutput,
    side_effects="none",
    timeout_s=20,
)

STUB_OUTPUT = PredictComponentRulOutput(
    items=[
        ComponentRisk(
            tail="SU-A0123",
            component="engine",
            hours_since_overhaul=120.0,
            p_fail_next=0.05,
            rul_p10=40.0,
            rul_p50=80.0,
            rul_p90=140.0,
        )
    ],
    aircraft_risk={"SU-A0123": 0.05},
    recommended_maintenance=[MaintenanceAdvice(tail="SU-A0123", component="engine", action="inspect")],
)


async def predict_component_rul(payload: PredictComponentRulInput, ctx: ToolContext) -> PredictComponentRulOutput:
    """Skeleton: same output for every input until the model is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_component_rul)
