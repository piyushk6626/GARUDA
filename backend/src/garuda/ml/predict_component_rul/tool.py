import asyncio
import logging
import math

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.ml.predict_component_rul.explain import explain
from garuda.ml.predict_component_rul.features import features
from garuda.ml.predict_component_rul.predict import (
    COMPONENT_TYPES,
    WeibullParameters,
    predict,
    validate_artifact,
)
from garuda.ml.predict_component_rul.schemas import (
    ComponentRisk,
    MaintenanceAdvice,
    PredictComponentRulInput,
    PredictComponentRulOutput,
)

logger = logging.getLogger(__name__)

SPEC = ToolSpec(
    name="predict_component_rul",
    description="Component failure probability and remaining useful life.",
    input_model=PredictComponentRulInput,
    output_model=PredictComponentRulOutput,
    side_effects="none",
    timeout_s=20,
)

# Compatibility fixture for the separate graph scaffold; inference never uses it.
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
    recommended_maintenance=[
        MaintenanceAdvice(tail="SU-A0123", component="engine", action="inspect")
    ],
)


async def predict_component_rul(
    payload: PredictComponentRulInput, ctx: ToolContext
) -> PredictComponentRulOutput:
    """Predict conditional failure and remaining hours from one scenario snapshot."""
    return await asyncio.to_thread(_evaluate, payload, ctx)


def _evaluate(
    payload: PredictComponentRulInput, ctx: ToolContext
) -> PredictComponentRulOutput:
    horizon = payload.sortie_hours_ahead
    if not math.isfinite(horizon) or horizon < 0:
        raise ToolInputError("sortie_hours_ahead must be finite and nonnegative")
    requested = set(
        COMPONENT_TYPES if payload.component_types is None else payload.component_types
    )
    unknown = sorted(requested - set(COMPONENT_TYPES))
    if unknown:
        raise ToolInputError(
            f"unknown component_types: {', '.join(unknown)}; valid: {', '.join(COMPONENT_TYPES)}"
        )
    components = [component for component in COMPONENT_TYPES if component in requested]
    rows = features(ctx.scenario.snapshot(), payload.tail_nos, components)
    if not rows:
        return PredictComponentRulOutput(
            items=[], aircraft_risk={}, recommended_maintenance=[]
        )
    try:
        artifact = ctx.models.load(SPEC.name)
    except (KeyError, FileNotFoundError, NotImplementedError):
        artifact = None
    if artifact is None:
        logger.warning(
            "Component RUL uses uncalibrated Weibull baseline and inspection-age proxy"
        )
    else:
        artifact = validate_artifact(artifact, components)
    items, advice = [], []
    probabilities_by_tail: dict[str, list[float]] = {}
    for row in rows:
        parameters = (
            artifact.parameters[row.component]
            if artifact is not None
            else WeibullParameters()
        )
        try:
            probability, p10, p50, p90 = predict(parameters, row.age_hours, horizon)
        except (ValueError, OverflowError) as exc:
            raise ValueError(
                f"cannot predict RUL for {row.tail}/{row.component}: {exc}"
            ) from exc
        item = ComponentRisk(
            tail=row.tail,
            component=row.component,
            hours_since_overhaul=row.age_hours,
            p_fail_next=probability,
            rul_p10=p10,
            rul_p50=p50,
            rul_p90=p90,
        )
        items.append(item)
        probabilities_by_tail.setdefault(row.tail, []).append(probability)
        recommendation = explain(item, horizon, row.inspection_hours_remaining)
        if recommendation is not None:
            advice.append(recommendation)
    aircraft_risk = {
        tail: (
            1.0
            if 1.0 in probabilities
            else -math.expm1(math.fsum(math.log1p(-p) for p in probabilities))
        )
        for tail, probabilities in probabilities_by_tail.items()
    }
    return PredictComponentRulOutput(
        items=items, aircraft_risk=aircraft_risk, recommended_maintenance=advice
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, predict_component_rul)
