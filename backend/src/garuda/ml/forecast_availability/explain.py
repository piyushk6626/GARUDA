"""Top three model SHAP drivers, or explicitly labelled rule explanations."""

from datetime import datetime
import logging

from garuda.ml.forecast_availability.features import AircraftFeatures, blocking_reason
from garuda.ml.forecast_availability.predict import ForecastAvailabilityArtifact

logger = logging.getLogger(__name__)


def rule_drivers(row: AircraftFeatures, at: datetime, horizons: list[int]) -> list[str]:
    drivers = []
    for horizon in horizons:
        reason = blocking_reason(row, at, horizon)
        if reason:
            drivers.append(f"T+{horizon} h: {reason}")
    airframe = row.aircraft
    drivers.append(f"Baseline: current status {airframe.status}")
    if airframe.maint_return_eta:
        drivers.append(
            f"Baseline: maintenance return ETA {airframe.maint_return_eta.isoformat()}"
        )
    drivers.append(
        f"Baseline: {airframe.hours_to_next_inspection:g} h to next inspection"
    )
    if row.sorties:
        drivers.append(f"Baseline: {len(row.sorties)} planned sorties")
    return list(dict.fromkeys(drivers))[:3]


def explain(
    artifact: ForecastAvailabilityArtifact | None,
    frames: dict,
    rows: list[AircraftFeatures],
    at: datetime,
    horizons: list[int],
) -> list[list[str]]:
    fallback = [rule_drivers(row, at, horizons) for row in rows]
    if artifact is None:
        return fallback
    try:
        import numpy as np

        horizon = horizons[0]
        frame = frames[horizon]
        explainer = artifact.explainers.get(horizon)
        if explainer is None:
            import shap

            explainer = shap.TreeExplainer(artifact.models[horizon])
        output = (
            explainer(frame) if callable(explainer) else explainer.shap_values(frame)
        )
        if isinstance(output, list):
            classes = list(getattr(artifact.models[horizon], "classes_", [0, 1]))
            output = output[classes.index(1)]
        elif hasattr(output, "values"):
            output = output.values
        values = np.asarray(output, dtype=float)
        if values.ndim == 3 and values.shape[2] == 2:
            classes = list(getattr(artifact.models[horizon], "classes_", [0, 1]))
            values = values[:, :, classes.index(1)]
        if values.shape != frame.shape or not np.isfinite(values).all():
            raise ValueError("SHAP output does not match the input feature coordinates")
        result = []
        for index, row in enumerate(rows):
            reason = blocking_reason(row, at, horizon)
            drivers = [f"T+{horizon} h: {reason}"] if reason else []
            order = sorted(
                range(len(frame.columns)), key=lambda i: (-abs(values[index, i]), i)
            )
            for column in order:
                if values[index, column] == 0:
                    continue
                name = frame.columns[column]
                drivers.append(
                    f"T+{horizon} h: {name}={frame.iloc[index, column]} "
                    f"(SHAP {values[index, column]:+.3g})",
                )
                if len(drivers) == 3:
                    break
            result.append(drivers or fallback[index])
        return result
    except Exception:
        logger.warning(
            "Availability SHAP explanation unavailable; using rule drivers",
            exc_info=True,
        )
        return fallback
