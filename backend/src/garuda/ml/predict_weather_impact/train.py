"""Offline calibrated LightGBM training using scenario forecasts and simulated outcomes."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from garuda.common import as_utc
from garuda.contracts.tool import ToolContext
from garuda.generators.weather import ceiling_ft, flight_category, flyability, snap_half
from garuda.ml.predict_weather_impact.features import (
    FEATURE_NAMES,
    GUIDANCE_TYPES,
    WeatherFeatures,
    WeatherIndex,
    features,
)
from garuda.ml.predict_weather_impact.predict import CLASSES, WeatherImpactArtifact
from garuda.ml.predict_weather_impact.schemas import PredictWeatherImpactInput
from garuda.queries.weather import _nearest_hour
from garuda.schemas.mission import GeneratedScenario
from garuda.schemas.weather import WeatherCell


def train(ctx: ToolContext, *, n_samples: int = 2000) -> WeatherImpactArtifact:
    """Return an artifact; callers explicitly persist it through ctx.models.save.

    Training is never performed by the inference tool. Metrics use held-out
    simulated outcomes and do not substitute for operational/METAR validation.
    """
    return _fit(ctx.scenario.snapshot(), ctx.scenario_time, ctx.seed, n_samples)


def _fit(
    snapshot: GeneratedScenario, as_of: datetime, seed: int, n_samples: int
) -> WeatherImpactArtifact:
    from lightgbm import LGBMClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import log_loss, roc_auc_score
    from sklearn.model_selection import train_test_split

    if n_samples < 300:
        raise ValueError(
            "use at least 300 training samples for three-class calibration"
        )
    eligible = [
        cell for cell in snapshot.cells if as_utc(cell.issued_at) <= as_utc(as_of)
    ]
    if not eligible or not snapshot.bases or not snapshot.aircraft_types:
        raise ValueError("training needs issued forecasts, bases, and aircraft types")
    climate = _climatology(eligible)
    index = WeatherIndex(eligible, as_of, climate)
    moments = sorted({as_utc(cell.time) for cell in eligible})
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    for _ in range(n_samples):
        mission = (
            snapshot.missions[int(rng.integers(len(snapshot.missions)))]
            if snapshot.missions
            else None
        )
        payload = PredictWeatherImpactInput(
            mission_id=mission.mission_id if mission else None,
            launch_base=snapshot.bases[int(rng.integers(len(snapshot.bases)))].base_id,
            start_time=moments[int(rng.integers(len(moments)))],
            aircraft_type=snapshot.aircraft_types[
                int(rng.integers(len(snapshot.aircraft_types)))
            ].type,
            weapon_guidance=GUIDANCE_TYPES[int(rng.integers(len(GUIDANCE_TYPES)))],
        )
        row = features(snapshot, payload, index)
        values = dict(row.values)
        # Simulator stress cases supply adverse-weather coverage even for a VMC fixture.
        # Keep half the rows as the actual aggregated forecast (including Open-Meteo).
        if rng.random() < 0.5:
            _stress_weather(values, rng)
        simulated = WeatherFeatures(values, ())
        rows.append(simulated.vector())
        labels.append(_outcome(values, rng))
    x, y = np.asarray(rows), np.asarray(labels)
    if any(np.count_nonzero(y == name) < 10 for name in CLASSES):
        raise ValueError(
            "training needs at least ten examples of every weather outcome"
        )
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.25, stratify=y, random_state=seed
    )
    estimator = CalibratedClassifierCV(
        estimator=LGBMClassifier(
            n_estimators=100,
            num_leaves=15,
            learning_rate=0.06,
            random_state=seed,
            n_jobs=1,
            verbosity=-1,
        ),
        method="sigmoid",
        cv=3,
        ensemble=False,
        n_jobs=1,
    )
    estimator.fit(x_train, y_train)
    probabilities = estimator.predict_proba(x_test)
    classes = list(estimator.classes_)
    truth = np.column_stack([y_test == name for name in classes]).astype(float)
    metrics = {
        "simulated_auc_ovr": float(
            roc_auc_score(y_test, probabilities, multi_class="ovr", labels=classes)
        ),
        "simulated_log_loss": float(log_loss(y_test, probabilities, labels=classes)),
        "simulated_brier": float(np.mean(np.sum((probabilities - truth) ** 2, axis=1))),
        "simulated_ece": _calibration_error(probabilities, truth),
        "training_samples": float(len(x_train)),
        "validation_samples": float(len(x_test)),
    }
    metrics.update(backtest_metar(snapshot))
    return WeatherImpactArtifact(estimator, climate, FEATURE_NAMES, metrics)


def backtest_metar(snapshot: GeneratedScenario) -> dict[str, float]:
    """Match real Open-Meteo flight-category forecasts to METARs at scenario bases.

    Each observation uses the latest forecast issued before it; synthetic rows
    and observations without a matching forecast are excluded.
    """
    bases = {base.icao: base for base in snapshot.bases}
    forecasts: dict[tuple, list[WeatherCell]] = {}
    for cell in snapshot.cells:
        if cell.source == "open-meteo":
            key = (round(cell.lat, 1), round(cell.lon, 1), as_utc(cell.time))
            forecasts.setdefault(key, []).append(cell)
    matches = []
    for observation in snapshot.observations:
        base = bases.get(observation.icao)
        if base is None or observation.source != "metar":
            continue
        key = (
            snap_half(base.lat),
            snap_half(base.lon),
            _nearest_hour(observation.obs_time),
        )
        eligible = [
            cell
            for cell in forecasts.get(key, [])
            if as_utc(cell.issued_at) <= as_utc(observation.obs_time)
        ]
        if eligible:
            cell = max(eligible, key=lambda item: as_utc(item.issued_at))
            matches.append(
                flight_category(cell.ceiling_ft, cell.visibility_km)
                == observation.flight_category
            )
    if not matches:
        return {}
    return {
        "metar_flight_category_accuracy": float(np.mean(matches)),
        "metar_samples": float(len(matches)),
    }


def _climatology(cells: list[WeatherCell]) -> WeatherCell:
    """Training-population medians; the same fallback is stored with the model."""
    names = (
        "cloud_low_pct",
        "cloud_mid_pct",
        "cloud_high_pct",
        "visibility_km",
        "wind_kts",
        "gust_kts",
        "precip_mm_h",
        "cape_jkg",
        "temp_c",
        "freezing_level_ft",
    )
    values = {
        name: float(np.median([getattr(cell, name) for cell in cells]))
        for name in names
    }
    values["ceiling_ft"] = ceiling_ft(values["cloud_low_pct"])
    values["cb_flag"] = values["cape_jkg"] > 1000
    values["flyability"] = flyability(
        visibility_km=values["visibility_km"],
        ceiling=values["ceiling_ft"],
        wind_kts=max(values["wind_kts"], values["gust_kts"]),
        cape_jkg=values["cape_jkg"],
        temp_c=values["temp_c"],
        cloud_low_pct=values["cloud_low_pct"],
        cloud_mid_pct=values["cloud_mid_pct"],
    )
    direction = np.mean([np.exp(1j * np.deg2rad(cell.wind_dir_deg)) for cell in cells])
    values["wind_dir_deg"] = float(np.rad2deg(np.angle(direction)) % 360)
    return WeatherCell.model_validate({**cells[0].model_dump(), **values})


def _stress_weather(values: dict[str, float], rng: np.random.Generator) -> None:
    case = int(rng.integers(5))
    if case == 0:
        for phase in ("launch", "recovery"):
            values[f"{phase}_vis_km"] = float(rng.uniform(0.2, 5))
            values[f"{phase}_ceiling_ft"] = float(rng.uniform(200, 2000))
            values[f"{phase}_flyability"] = min(
                values[f"{phase}_flyability"], values[f"{phase}_vis_km"] / 8
            )
    elif case == 1:
        phase = "launch" if rng.random() < 0.5 else "recovery"
        values[f"{phase}_crosswind_kts"] = float(rng.uniform(15, 40))
        values[f"{phase}_precip_mm_h"] = float(rng.uniform(0, 12))
    elif case == 2:
        values["route_cb_fraction"] = float(rng.uniform(0.1, 1))
        values["route_min_flyability"] = float(rng.uniform(0, 0.6))
    elif case == 3:
        values["route_icing"] = 1.0
        values["route_min_flyability"] = 0.0
    else:
        values["target_cloud_low_pct"] = float(rng.uniform(50, 100))
        values["target_ceiling_ft"] = float(rng.uniform(200, 2500))
        values["target_vis_km"] = float(rng.uniform(0.5, 6))


def _outcome(values: dict[str, float], rng: np.random.Generator) -> str:
    # Forecast error is multiplicative and grows by approximately 0.1 per 12h.
    sigma = 0.03 + 0.1 * values["lead_hours"] / 12
    noisy = dict(values)
    for name in FEATURE_NAMES:
        if any(token in name for token in ("ceiling_ft", "vis_km", "crosswind_kts")):
            noisy[name] *= float(np.exp(rng.normal(0, sigma)))
    no_go = any(
        noisy[f"{phase}_vis_km"] < 3
        or noisy[f"{phase}_crosswind_kts"] > 25
        or noisy[f"{phase}_ceiling_ft"] < 500
        for phase in ("launch", "recovery")
    )
    no_go |= noisy["route_icing"] > 0 or noisy["route_cb_fraction"] > 0.25
    visual = (
        noisy["guidance_LASER"]
        or noisy["guidance_IR"]
        or (
            noisy["guidance_NONE"] and (noisy["mission_STRIKE"] or noisy["mission_CAS"])
        )
    )
    degraded = (
        noisy["route_min_flyability"] < 0.7
        or noisy["route_cb_fraction"] > 0
        or max(noisy["launch_precip_mm_h"], noisy["recovery_precip_mm_h"]) > 5
    )
    degraded |= bool(
        visual and (noisy["target_ceiling_ft"] < 1500 or noisy["target_vis_km"] < 3)
    )
    degraded |= bool(
        (noisy["guidance_LASER"] or noisy["guidance_IR"])
        and noisy["target_cloud_low_pct"] >= 50
    )
    outcome = "NO_GO" if no_go else "DEGRADED" if degraded else "GO"
    return str(rng.choice(CLASSES)) if rng.random() < 0.03 else outcome


def _calibration_error(probabilities: np.ndarray, truth: np.ndarray) -> float:
    errors = []
    for column in range(3):
        error = 0.0
        for lower in np.arange(0, 1, 0.1):
            mask = (probabilities[:, column] >= lower) & (
                probabilities[:, column] < lower + 0.1
            )
            if mask.any():
                error += float(
                    mask.mean()
                    * abs(
                        probabilities[mask, column].mean() - truth[mask, column].mean()
                    )
                )
        errors.append(error)
    return float(np.mean(errors))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train an offline GARUDA weather-impact model"
    )
    parser.add_argument("--scenario-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()
    import joblib

    snapshot = GeneratedScenario.model_validate_json(
        args.scenario_json.read_text(encoding="utf-8")
    )
    if not snapshot.cells:
        parser.error("the scenario has no training forecasts")
    as_of = min(as_utc(cell.issued_at) for cell in snapshot.cells)
    artifact = _fit(snapshot, as_of, snapshot.seed, args.samples)
    joblib.dump(artifact, args.output)
    print(json.dumps(artifact.metrics, sort_keys=True))


if __name__ == "__main__":
    main()
