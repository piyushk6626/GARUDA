import asyncio
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from garuda.config import load_config
from garuda.contracts.geo import LatLon
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.generators.weather import snap_half
from garuda.ml.predict_weather_impact.features import (
    FEATURE_NAMES,
    WeatherIndex,
    features,
    resolve_flight,
)
from garuda.ml.predict_weather_impact.predict import WeatherImpactArtifact, predict
from garuda.ml.predict_weather_impact.schemas import PredictWeatherImpactInput
from garuda.ml.predict_weather_impact.tool import SPEC, predict_weather_impact
from garuda.ml.predict_weather_impact.train import backtest_metar, train

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


class ForecastClassifier:
    """A deterministic test estimator with deliberately noncanonical class order."""

    classes_ = np.asarray(["NO_GO", "GO", "DEGRADED"])

    def predict_proba(self, matrix):
        self.last_matrix = matrix
        rows = []
        for row in matrix:
            values = dict(zip(FEATURE_NAMES, row))
            bad = (
                min(values["launch_vis_km"], values["recovery_vis_km"]) < 3
                or values["route_icing"]
            )
            degraded = (values["guidance_LASER"] or values["guidance_IR"]) and values[
                "target_cloud_low_pct"
            ] >= 50
            rows.append(
                [0.85, 0.05, 0.1]
                if bad
                else [0.05, 0.35, 0.6]
                if degraded
                else [0.01, 0.95, 0.04]
            )
        return np.asarray(rows)


class Store:
    def __init__(self, artifact):
        self.artifact = artifact
        self.loads = []

    def load(self, name, version=None):
        self.loads.append(name)
        if isinstance(self.artifact, Exception):
            raise self.artifact
        return self.artifact

    def save(self, *args):
        pytest.fail("inference must never write to the model store")


@pytest.fixture(scope="module")
def scenario():
    return build_scenario(load_config(CONFIG_PATH), 42)


def context(scenario, artifact=None):
    if artifact is None:
        artifact = WeatherImpactArtifact(ForecastClassifier(), scenario.cells[0])
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        plans=None,
        models=Store(artifact),
        providers=None,
        scenario_time=load_config(CONFIG_PATH).start,
        seed=42,
    )


def payload(scenario, **updates):
    return PredictWeatherImpactInput(
        launch_base="B5",
        aircraft_type="Rafale",
        start_time=scenario.cells[0].time,
        **updates,
    )


def run(payload, ctx):
    return asyncio.run(predict_weather_impact(payload, ctx))


def test_contract_is_unchanged():
    assert list(SPEC.input_model.model_fields) == [
        "mission_id",
        "route",
        "launch_base",
        "recovery_base",
        "start_time",
        "aircraft_type",
        "weapon_guidance",
    ]
    assert list(SPEC.output_model.model_fields) == [
        "p_go",
        "p_degraded",
        "p_no_go",
        "limiting_factors",
        "better_windows",
        "alternates",
    ]
    assert SPEC.side_effects == "none" and SPEC.timeout_s == 10


def test_clear_and_fog_are_input_sensitive_and_normalized(scenario):
    ctx = context(scenario)
    request = payload(scenario)
    before = request.model_dump_json()
    clear = run(request, ctx)
    fog_request = request.model_copy(
        update={"start_time": request.start_time + timedelta(hours=20)}
    )
    fog = run(fog_request, ctx)
    assert clear.p_go == pytest.approx(0.95)
    assert fog.p_no_go == pytest.approx(0.85)
    assert fog.p_go < clear.p_go
    assert "launch_visibility" in fog.limiting_factors
    for output in (clear, fog):
        assert output.p_go + output.p_degraded + output.p_no_go == pytest.approx(1)
    assert request.model_dump_json() == before
    assert ctx.models.loads == ["predict_weather_impact", "predict_weather_impact"]
    assert (
        ctx.models.artifact.estimator.last_matrix.shape[0] == 18
    )  # One batched inference.


def test_better_windows_wait_until_fog_clears(scenario):
    request = payload(scenario).model_copy(
        update={"start_time": scenario.cells[0].time + timedelta(hours=20)}
    )
    result = run(request, context(scenario))
    assert result.better_windows
    assert result.better_windows[0].start == request.start_time + timedelta(hours=6)
    assert all(
        request.start_time
        < window.start
        < window.end
        <= request.start_time + timedelta(hours=12)
        for window in result.better_windows
    )
    assert len(result.better_windows) <= 3
    # Changing only recovery cannot cure fog at launch.
    assert result.alternates == []


def test_alternate_recovery_preserves_launch(scenario):
    request = payload(scenario).model_copy(
        update={
            "launch_base": "B1",
            "recovery_base": "B5",
            "start_time": scenario.cells[0].time + timedelta(hours=20),
        }
    )
    result = run(request, context(scenario))
    assert "recovery_visibility" in result.limiting_factors
    assert result.alternates
    assert "B5" not in {item.base_id for item in result.alternates}
    assert all(item.p_go > result.p_go for item in result.alternates)
    assert result.alternates == sorted(
        result.alternates, key=lambda item: (-item.p_go, item.base_id)
    )


@pytest.mark.parametrize(
    "missing", [KeyError("model"), FileNotFoundError("model"), NotImplementedError()]
)
def test_missing_trained_model_is_an_error(scenario, missing):
    with pytest.raises(
        RuntimeError, match="trained predict_weather_impact model is required"
    ):
        run(payload(scenario), context(scenario, missing))


def test_none_model_is_an_error(scenario):
    ctx = context(scenario)
    ctx.models.artifact = None
    with pytest.raises(
        RuntimeError, match="trained predict_weather_impact model is required"
    ):
        run(payload(scenario), ctx)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"launch_base": "missing"}, "unknown base"),
        ({"recovery_base": "missing"}, "unknown base"),
        ({"aircraft_type": "missing"}, "unknown aircraft_type"),
        ({"mission_id": "missing"}, "unknown mission_id"),
        ({"route": []}, "at least one point"),
    ],
)
def test_bad_inputs_have_actionable_errors(scenario, changes, message):
    request = payload(scenario).model_copy(update=changes)
    with pytest.raises(ToolInputError, match=message):
        run(request, context(scenario))


def test_missing_forecasts_use_training_climatology_and_flag_confidence(scenario):
    missing = scenario.model_copy(update={"cells": []})
    result = run(
        payload(scenario),
        context(
            missing, WeatherImpactArtifact(ForecastClassifier(), scenario.cells[0])
        ),
    )
    assert {"missing_forecast_climatology", "low_confidence"} <= set(
        result.limiting_factors
    )
    assert result.better_windows == [] and result.alternates == []


def test_forecast_revisions_respect_scenario_time(scenario):
    ctx = context(scenario)
    cell = scenario.cells[0].model_copy(
        update={"issued_at": ctx.scenario_time - timedelta(hours=1)}
    )
    newer = cell.model_copy(update={"visibility_km": 1, "issued_at": ctx.scenario_time})
    future = cell.model_copy(
        update={
            "visibility_km": 0.2,
            "issued_at": ctx.scenario_time + timedelta(hours=1),
        }
    )
    index = WeatherIndex([cell, newer, future], ctx.scenario_time, cell)
    found, fallback = index.lookup((cell.lat, cell.lon), cell.time)
    assert not fallback and found.visibility_km == 1


def test_target_cloud_affects_laser_and_ir_more_than_gps(scenario):
    target = LatLon(lat=26, lon=72)
    cloudy = [
        cell.model_copy(update={"cloud_low_pct": 80, "ceiling_ft": 1000})
        if (cell.lat, cell.lon) == (26, 72)
        else cell
        for cell in scenario.cells
    ]
    snapshot = scenario.model_copy(update={"cells": cloudy})
    request = payload(snapshot, route=[target])
    for guidance in ("LASER", "IR"):
        result = run(
            request.model_copy(update={"weapon_guidance": guidance}), context(snapshot)
        )
        assert (
            "guidance_cloud" in result.limiting_factors
            and "target_ceiling" in result.limiting_factors
        )
        assert result.p_degraded == pytest.approx(0.6)
    gps = run(request.model_copy(update={"weapon_guidance": "GPS"}), context(snapshot))
    assert "guidance_cloud" not in gps.limiting_factors and gps.p_go == pytest.approx(
        0.95
    )


def test_crosswind_uses_runway_heading_and_gusts(scenario):
    base = next(item for item in scenario.bases if item.base_id == "B1")
    cells = [
        cell.model_copy(update={"wind_kts": 5, "gust_kts": 30, "wind_dir_deg": 140})
        if (cell.lat, cell.lon) == (snap_half(base.lat), snap_half(base.lon))
        else cell
        for cell in scenario.cells
    ]
    snapshot = scenario.model_copy(update={"cells": cells})
    request = payload(snapshot).model_copy(update={"launch_base": "B1"})
    ctx = context(snapshot)
    row = features(snapshot, request, WeatherIndex(cells, ctx.scenario_time, cells[0]))
    assert row.values["launch_crosswind_kts"] == pytest.approx(30)
    assert "launch_crosswind" in row.limiting_factors


def test_route_icing_and_cb_are_detected(scenario):
    cells = [
        cell.model_copy(
            update={
                "temp_c": -5,
                "cloud_mid_pct": 80,
                "cape_jkg": 1500,
                "cb_flag": True,
            }
        )
        for cell in scenario.cells
    ]
    snapshot = scenario.model_copy(update={"cells": cells})
    result = run(payload(snapshot, route=[LatLon(lat=26, lon=72)]), context(snapshot))
    assert {"route_icing", "route_cb"} <= set(result.limiting_factors)


def test_mission_resolves_target_and_recovery_time(scenario):
    mission = next(item for item in scenario.missions if item.target_id)
    target = next(
        item for item in scenario.targets if item.target_id == mission.target_id
    )
    request = payload(scenario, mission_id=mission.mission_id)
    flight = resolve_flight(request, scenario)
    assert flight.target == (target.lat, target.lon)
    assert flight.recovery_hours >= mission.duration_min / 60
    assert flight.target_hours > 0
    ctx = context(scenario)
    row = features(
        scenario,
        request,
        WeatherIndex(scenario.cells, ctx.scenario_time, scenario.cells[0]),
        flight,
    )
    assert row.values[f"mission_{mission.type}"] == 1
    assert "base_only_assessment" not in row.limiting_factors


def test_timezone_equivalence_and_naive_utc(scenario):
    request = payload(scenario)
    local = request.model_copy(
        update={
            "start_time": request.start_time.astimezone(
                timezone(timedelta(hours=5, minutes=30))
            )
        }
    )
    naive = request.model_copy(
        update={"start_time": request.start_time.replace(tzinfo=None)}
    )
    ctx = context(scenario)
    assert run(request, ctx) == run(local, ctx) == run(naive, ctx)


def test_incompatible_artifact_fails_instead_of_falling_back(scenario):
    artifact = replace(context(scenario).models.artifact, feature_names=("wrong",))
    with pytest.raises(ValueError, match="incompatible feature"):
        run(payload(scenario), context(scenario, artifact))


@pytest.mark.parametrize(
    "bad", [[float("nan"), 0, 1], [-0.1, 0.5, 0.6], [0.1, 0.1, 0.1]]
)
def test_invalid_model_probabilities_are_rejected(scenario, bad):
    class BadClassifier(ForecastClassifier):
        def predict_proba(self, matrix):
            return np.tile(bad, (len(matrix), 1))

    artifact = WeatherImpactArtifact(BadClassifier(), scenario.cells[0])
    with pytest.raises(ValueError, match="probabilit"):
        run(payload(scenario), context(scenario, artifact))


def test_real_calibrated_training_and_artifact_round_trip(scenario, tmp_path):
    import joblib

    ctx = context(scenario)
    artifact = train(ctx, n_samples=600)
    assert set(artifact.estimator.classes_) == {"GO", "DEGRADED", "NO_GO"}
    assert 0.5 < artifact.metrics["simulated_auc_ovr"] <= 1
    assert 0 <= artifact.metrics["simulated_ece"] <= 1
    path = tmp_path / "weather.joblib"
    joblib.dump(artifact, path)
    loaded = joblib.load(path)
    row = features(
        scenario,
        payload(scenario),
        WeatherIndex(scenario.cells, ctx.scenario_time, loaded.climatology),
    )
    assert np.allclose(predict(loaded, [row]), predict(artifact, [row]))
    output = run(payload(scenario), context(scenario, loaded))
    assert output.p_go + output.p_no_go + output.p_degraded == pytest.approx(1)
    fog_request = payload(scenario).model_copy(
        update={"start_time": scenario.cells[0].time + timedelta(hours=20)}
    )
    fog = run(fog_request, context(scenario, loaded))
    assert fog.p_no_go > output.p_no_go and fog.p_go < output.p_go


def test_metar_backtest_excludes_synthetic_and_future_issued_forecasts(scenario):
    assert backtest_metar(scenario) == {}
    observation = scenario.observations[0].model_copy(update={"source": "metar"})
    base = next(base for base in scenario.bases if base.icao == observation.icao)
    cell = next(
        cell
        for cell in scenario.cells
        if cell.time == observation.obs_time
        and cell.lat == snap_half(base.lat)
        and cell.lon == snap_half(base.lon)
    )
    cell = cell.model_copy(update={"source": "open-meteo"})
    future = cell.model_copy(
        update={"issued_at": cell.time + timedelta(hours=1), "visibility_km": 0.1}
    )
    snapshot = scenario.model_copy(
        update={"cells": [cell, future], "observations": [observation]}
    )
    assert backtest_metar(snapshot) == {
        "metar_flight_category_accuracy": 1.0,
        "metar_samples": 1.0,
    }
    assert backtest_metar(snapshot.model_copy(update={"cells": [future]})) == {}


def test_diversion_keeps_target_when_it_is_at_recovery(scenario):
    base = next(item for item in scenario.bases if item.base_id == "B5")
    request = payload(
        scenario, route=[LatLon(lat=base.lat, lon=base.lon)], recovery_base="B5"
    )
    flight = resolve_flight(request, scenario)
    assert flight.route[-2] == flight.target


def test_explicit_route_must_reach_mission_target(scenario):
    mission = next(item for item in scenario.missions if item.target_id)
    request = payload(
        scenario, mission_id=mission.mission_id, route=[LatLon(lat=22, lon=68)]
    )
    with pytest.raises(ToolInputError, match="must pass within 25 km"):
        resolve_flight(request, scenario)


def test_better_windows_respect_mission_duration_and_window(scenario):
    start = scenario.cells[0].time + timedelta(hours=20)
    mission = scenario.missions[0].model_copy(
        update={
            "target_id": None,
            "area_wkt": None,
            "duration_min": 30,
            "window_start": start,
            "window_end": start + timedelta(hours=7, minutes=30),
        }
    )
    snapshot = scenario.model_copy(update={"missions": [mission]})
    request = payload(snapshot, mission_id=mission.mission_id).model_copy(
        update={"start_time": start}
    )
    result = run(request, context(snapshot))
    assert result.better_windows
    assert all(
        window.end + timedelta(minutes=30) <= mission.window_end
        for window in result.better_windows
    )
