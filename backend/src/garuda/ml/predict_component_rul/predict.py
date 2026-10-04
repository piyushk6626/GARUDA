"""Conditional Weibull inference; no fitting or persistence during tool calls."""

from dataclasses import dataclass
import math


COMPONENT_TYPES = ("engine", "radar", "hydraulics", "avionics")


@dataclass(frozen=True)
class WeibullParameters:
    shape: float = 2.2
    scale_hours: float = 800.0

    def __post_init__(self):
        for name, value in (("shape", self.shape), ("scale_hours", self.scale_hours)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Weibull {name} must be finite and positive")


@dataclass(frozen=True)
class ComponentRulArtifact:
    """Per-component parameters supplied by the existing ModelStore port."""

    parameters: dict[str, WeibullParameters]


def validate_artifact(artifact: object, components: list[str]) -> ComponentRulArtifact:
    if not isinstance(artifact, ComponentRulArtifact) or not isinstance(
        artifact.parameters, dict
    ):
        raise ValueError("predict_component_rul model must be a ComponentRulArtifact")
    for component in components:
        parameters = artifact.parameters.get(component)
        if not isinstance(parameters, WeibullParameters):
            raise ValueError(
                f"predict_component_rul model lacks parameters for {component}"
            )
        parameters.__post_init__()
    return artifact


def _log_expm1(value: float) -> float:
    return value if value > 50 else math.log(math.expm1(value))


def predict(
    parameters: WeibullParameters, age_hours: float, horizon_hours: float
) -> tuple[float, float, float, float]:
    """Return next-h failure probability and conditional RUL p10/p50/p90.

    Work in cumulative hazards to avoid S(t+h)/S(t) becoming 0/0 for old
    components. Quantiles are remaining hours, not absolute failure ages.
    """
    parameters.__post_init__()
    if not math.isfinite(age_hours) or age_hours < 0:
        raise ValueError("component age must be finite and nonnegative")
    if not math.isfinite(horizon_hours) or horizon_hours < 0:
        raise ValueError("failure horizon must be finite and nonnegative")
    shape, scale = parameters.shape, parameters.scale_hours
    log_hazard = (
        shape * (math.log(age_hours) - math.log(scale)) if age_hours else -math.inf
    )
    if not horizon_hours:
        probability = 0.0
    else:
        if not age_hours:
            log_delta = shape * (math.log(horizon_hours) - math.log(scale))
        else:
            ratio = horizon_hours / age_hours
            log_ratio = (
                math.log1p(ratio)
                if math.isfinite(ratio)
                else math.log(horizon_hours) - math.log(age_hours)
            )
            increment = shape * log_ratio
            if increment:
                log_delta = log_hazard + _log_expm1(increment)
            else:
                log_delta = (
                    log_hazard
                    + math.log(shape)
                    + math.log(horizon_hours)
                    - math.log(age_hours)
                )
        probability = 1.0 if log_delta > 7 else -math.expm1(-math.exp(log_delta))

    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Weibull failure probability exceeds the supported numeric range")
    quantiles = []
    for level in (0.1, 0.5, 0.9):
        hazard_increment = -math.log1p(-level)
        if not age_hours:
            remaining = scale * hazard_increment ** (1 / shape)
        else:
            log_ratio = math.log(hazard_increment) - log_hazard
            if log_ratio < -36:
                remaining = math.exp(math.log(age_hours) + log_ratio - math.log(shape))
            elif log_ratio > 36:
                # H(t) is negligible. Avoid overflowing expm1 times a tiny t.
                remaining = (
                    math.exp(math.log(scale) + math.log(hazard_increment) / shape)
                    - age_hours
                )
            else:
                softplus = (
                    log_ratio + math.log1p(math.exp(-log_ratio))
                    if log_ratio > 0
                    else math.log1p(math.exp(log_ratio))
                )
                remaining = age_hours * math.expm1(softplus / shape)
        if not math.isfinite(remaining):
            raise ValueError("Weibull RUL exceeds the supported numeric range")
        quantiles.append(remaining)
    return probability, *quantiles
