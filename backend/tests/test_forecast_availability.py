import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import product
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from garuda.config import load_config
from garuda.contracts.plan import Assignment, PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.ml.forecast_availability.features import (
    FEATURE_NAMES,
    features,
    horizon_features,
)
from garuda.ml.forecast_availability.predict import (
    BASELINE_VERSION,
    ForecastAvailabilityArtifact,
    fleet_forecast,
)
from garuda.ml.forecast_availability.schemas import (
    ForecastAvailabilityInput,
    ForecastAvailabilityOutput,
)
from garuda.ml.forecast_availability.tool import SPEC, forecast_availability
from garuda.schemas.aircraft import Aircraft, AircraftHistory

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def airframe(tail="A1", **updates):
    values = dict(
        tail_no=tail,
        type="Rafale",
        sqn_id="SQ1",
        base_id="B1",
        status="FMC",
        fuel_kg=1000,
        fuel_capacity_kg=2000,
        flight_hours_total=1000,
        hours_to_next_inspection=80,
        inspection_cycle_hours=100,
        turnaround_min=30,
        capabilities=["ISR"],
        range_km=1000,
        cruise_kts=400,
        ceiling_ft=40000,
        updated_at=NOW,
    )
    values.update(updates)
    return Aircraft(**values)


class Models:
    def __init__(self, artifact=None, error=None):
        self.artifact = artifact
        self.error = error
        self.calls = []

    def load(self, name, version=None):
        self.calls.append((name, version))
        if self.error:
            raise self.error
        if self.artifact is None:
            raise FileNotFoundError(name)
        return self.artifact


class Plans:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    def get(self, plan_id):
        self.calls.append(plan_id)
        if self.plan is None or self.plan.plan_id != plan_id:
            raise KeyError(plan_id)
        return self.plan


def context(aircraft=None, history=None, plan=None, models=None, missions=None):
    scenario = SimpleNamespace(
        aircraft=[airframe()] if aircraft is None else aircraft,
        aircraft_history=history or [],
        missions=missions or [],
    )
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        plans=Plans(plan),
        models=models or Models(),
        providers=None,
        scenario_time=NOW,
    )


def run(ctx, **payload):
    return asyncio.run(forecast_availability(ForecastAvailabilityInput(**payload), ctx))


class Classifier:
    classes_ = np.array([0, 1])

    def __init__(self, probability):
        self.probability = probability
        self.frames = []

    def predict_proba(self, frame):
        self.frames.append(frame.copy())
        return np.tile([1 - self.probability, self.probability], (len(frame), 1))


class Calibrator:
    def predict(self, values):
        return values * 0.8


class Explainer:
    def __call__(self, frame):
        values = np.zeros(frame.shape)
        values[:, list(frame.columns).index("hours_to_next_inspection")] = 0.4
        values[:, list(frame.columns).index("flight_hours_total")] = -0.2
        values[:, list(frame.columns).index("current_status")] = 0.1
        return SimpleNamespace(values=values)


def artifact(**updates):
    values = dict(
        model_version="availability-v7",
        models={24: Classifier(0.75), 48: Classifier(0.5), 72: Classifier(0.25)},
        explainers={24: Explainer(), 48: Explainer(), 72: Explainer()},
    )
    values.update(updates)
    return ForecastAvailabilityArtifact(**values)


def slot(start_h=2, end_h=4, **updates):
    values = dict(
        slot_id="S1",
        mission_id="M1",
        tail_no="A1",
        role="pilot",
        aircraft_type="Rafale",
        start=NOW + timedelta(hours=start_h),
        end=NOW + timedelta(hours=end_h),
    )
    values.update(updates)
    return SortieSlot(**values)


def test_default_baseline_and_unchanged_output_contract():
    ctx = context([airframe("A2", base_id="B2"), airframe("A1")])
    output = run(ctx)
    assert isinstance(output, ForecastAvailabilityOutput)
    assert [item.tail for item in output.per_aircraft] == ["A1", "A2"]
    assert output.per_aircraft[0].prob_by_horizon == {"24": 1.0, "48": 1.0, "72": 1.0}
    assert set(output.fleet) == {"B1/Rafale", "B2/Rafale"}
    assert output.fleet["B1/Rafale"].model_dump() == {
        "mean": 1.0,
        "p10": 1.0,
        "p90": 1.0,
    }
    assert output.model_version == BASELINE_VERSION
    assert set(output.model_dump()) == {"per_aircraft", "fleet", "model_version"}
    assert (
        ForecastAvailabilityOutput.model_validate_json(output.model_dump_json())
        == output
    )
    assert SPEC.input_model is ForecastAvailabilityInput
    assert SPEC.output_model is ForecastAvailabilityOutput
    assert SPEC.side_effects == "none"


@pytest.mark.parametrize(
    "payload",
    [
        {"base_ids": []},
        {"types": []},
        {"base_ids": ["missing"]},
        {"types": ["missing"]},
    ],
)
def test_empty_matches_are_empty_and_do_not_load_models(payload):
    ctx = context()
    output = run(ctx, **payload)
    assert output.per_aircraft == [] and output.fleet == {}
    assert ctx.models.calls == []


def test_filters_are_combined_without_a_hidden_aircraft_limit():
    ctx = context(
        [airframe(f"A{i:03}") for i in range(225)] + [airframe("other", base_id="B2")]
    )
    assert len(run(ctx, base_ids=["B1"], types=["Rafale"]).per_aircraft) == 225
    assert len(run(ctx, base_ids=["B2"], types=["Tejas"]).per_aircraft) == 0


def test_calibrated_models_shap_and_first_requested_fleet_horizon():
    model = artifact(calibrators={24: Calibrator()})
    output = run(
        context([airframe("A1"), airframe("A2")], models=Models(model)),
        horizons_h=[48, 24, 72, 48],
    )
    assert output.model_version == "availability-v7"
    assert output.per_aircraft[0].prob_by_horizon == {
        "48": 0.5,
        "24": pytest.approx(0.6),
        "72": 0.25,
    }
    assert output.fleet["B1/Rafale"].model_dump() == {
        "mean": 1.0,
        "p10": 0.0,
        "p90": 2.0,
    }
    drivers = output.per_aircraft[0].top_drivers
    assert len(drivers) == 3
    assert "hours_to_next_inspection" in drivers[0] and "SHAP" in drivers[0]
    assert "T+48 h" in drivers[0]
    assert "flight_hours_total" in drivers[1]
    frame = model.models[24].frames[0]
    assert tuple(frame.columns) == FEATURE_NAMES
    assert frame["ground_crew_load"].isna().all()


@pytest.mark.parametrize("use_model", [False, True])
def test_maintenance_override_and_exact_return_boundary(use_model):
    plane = airframe(
        status="NMC",
        status_reason="scheduled_inspection",
        hours_to_next_inspection=0,
        maint_return_eta=NOW + timedelta(hours=48),
    )
    ctx = context([plane], models=Models(artifact()) if use_model else Models())
    output = run(ctx)
    values = output.per_aircraft[0].prob_by_horizon
    assert values["24"] == 0
    assert values["48"] == (0.5 if use_model else 1)
    assert values["72"] == (0.25 if use_model else 1)
    assert "Maintenance" in output.per_aircraft[0].top_drivers[0]


def test_baseline_statuses_and_eta_without_fabricated_repairs():
    ctx = context(
        [
            airframe("PMC", status="PMC", status_reason="deferred_defect"),
            airframe(
                "AOG",
                status="AOG",
                status_reason="awaiting_parts",
                maint_return_eta=NOW + timedelta(hours=30),
            ),
            airframe("MISSION", status="IN_MISSION"),
            airframe("DUE", hours_to_next_inspection=0),
        ]
    )
    values = {item.tail: item.prob_by_horizon for item in run(ctx).per_aircraft}
    assert (
        values["PMC"]
        == values["MISSION"]
        == values["DUE"]
        == {"24": 0, "48": 0, "72": 0}
    )
    assert values["AOG"] == {"24": 0, "48": 1, "72": 1}


def test_plan_slots_assignments_and_roles_are_not_double_counted():
    plan = PlanDraft(
        plan_id="P1",
        scenario_time=NOW,
        assignments=[
            Assignment(
                tail_no="A1",
                config_id="C1",
                mission_id="M1",
                depart=NOW + timedelta(hours=2),
                tot=NOW + timedelta(hours=3),
            )
        ],
        sortie_slots=[slot(), slot(slot_id="S2", role="copilot")],
    )
    ctx = context(plan=plan)
    before = (
        plan.model_dump_json(),
        ctx.scenario.snapshot().aircraft[0].model_dump_json(),
    )
    without = run(ctx)
    with_plan = run(ctx, include_planned_sorties_from="P1")
    assert without.per_aircraft[0].prob_by_horizon["24"] == 1
    assert with_plan.per_aircraft[0].prob_by_horizon["24"] == 0.96
    row = features(
        ctx.scenario.snapshot(), ctx.scenario.snapshot().aircraft, NOW, plan
    )[0]
    assert horizon_features(row, NOW, 24)["planned_sorties"] == 1
    assert horizon_features(row, NOW, 24)["planned_flight_hours"] == 2
    assert before == (
        plan.model_dump_json(),
        ctx.scenario.snapshot().aircraft[0].model_dump_json(),
    )


def test_assignment_only_plan_uses_mission_duration():
    plan = PlanDraft(
        plan_id="P1",
        scenario_time=NOW,
        assignments=[
            Assignment(
                tail_no="A1",
                config_id="C1",
                mission_id="M1",
                depart=NOW + timedelta(hours=2),
                tot=NOW + timedelta(hours=3),
            ),
        ],
    )
    ctx = context(
        plan=plan, missions=[SimpleNamespace(mission_id="M1", duration_min=120)]
    )
    row = features(
        ctx.scenario.snapshot(), ctx.scenario.snapshot().aircraft, NOW, plan
    )[0]
    assert horizon_features(row, NOW, 24)["planned_flight_hours"] == 2
    assert (
        run(ctx, include_planned_sorties_from="P1")
        .per_aircraft[0]
        .prob_by_horizon["24"]
        == 0.96
    )


def test_plan_active_sortie_turnaround_and_inspection_overrides():
    plan = PlanDraft(plan_id="P1", scenario_time=NOW, sortie_slots=[slot(23, 25)])
    ctx = context(plan=plan, models=Models(artifact()))
    output = run(ctx, include_planned_sorties_from="P1", horizons_h=[24, 25, 26])
    # Missing model horizons cause atomic fallback; known plan constraints still apply.
    assert output.model_version == BASELINE_VERSION
    assert output.per_aircraft[0].prob_by_horizon == {"24": 0, "25": 0, "26": 0.96}
    assert (
        run(
            context([airframe(hours_to_next_inspection=2)], plan=plan),
            include_planned_sorties_from="P1",
        )
        .per_aircraft[0]
        .prob_by_horizon["48"]
        == 0
    )
    assert run(
        context(plan=plan), include_planned_sorties_from="P1", horizons_h=[1]
    ).per_aircraft[0].prob_by_horizon == {"1": 1}


def test_current_mission_returns_when_the_plan_provides_its_end():
    plan = PlanDraft(plan_id="P1", scenario_time=NOW, sortie_slots=[slot(-1, 2)])
    output = run(
        context([airframe(status="IN_MISSION")], plan=plan),
        include_planned_sorties_from="P1",
    )
    assert output.per_aircraft[0].prob_by_horizon["24"] == 0.96


def test_model_receives_plan_features_and_hard_overrides():
    model = artifact()
    plan = PlanDraft(plan_id="P1", scenario_time=NOW, sortie_slots=[slot(23, 25)])
    output = run(
        context(plan=plan, models=Models(model)), include_planned_sorties_from="P1"
    )
    assert output.model_version == model.model_version
    assert output.per_aircraft[0].prob_by_horizon == {"24": 0, "48": 0.5, "72": 0.25}
    assert model.models[24].frames[0].iloc[0]["planned_flight_hours"] == 1
    assert model.models[48].frames[0].iloc[0]["planned_flight_hours"] == 2


def history(hours, status):
    return AircraftHistory(
        time=NOW + timedelta(hours=hours),
        tail_no="A1",
        status=status,
        base_id="B1",
        fuel_kg=1000,
        flight_hours_total=1000,
        hours_to_next_inspection=80,
    )


def test_history_counts_transitions_and_excludes_future_information():
    rows = [
        history(-80, "FMC"),
        history(-60, "IN_MISSION"),
        history(-59, "FMC"),
        history(-20, "IN_MISSION"),
        history(-19, "IN_MISSION"),
        history(-18, "NMC"),
        history(-12, "FMC"),
        history(-8, "FMC"),
        history(1, "NMC"),
    ]
    ctx = context(
        [airframe(updated_at=NOW - timedelta(hours=8))], history=list(reversed(rows))
    )
    row = features(ctx.scenario.snapshot(), ctx.scenario.snapshot().aircraft, NOW)[0]
    assert row.values["time_in_status_h"] == 12
    assert row.values["sorties_last_24h"] == 1
    assert row.values["sorties_last_72h"] == 2
    assert row.values["recent_failure_count"] == 1
    output = run(ctx)
    ctx_without_future = context(ctx.scenario.snapshot().aircraft, history=rows[:-1])
    assert output == run(ctx_without_future)


@pytest.mark.parametrize("horizons", [[], [0], [-1], [24, 0], [10**30]])
def test_invalid_horizons_raise_actionable_errors(horizons):
    with pytest.raises(ToolInputError, match="horizons_h"):
        run(context(), horizons_h=horizons)


def test_invalid_plan_references_and_timestamps():
    with pytest.raises(ToolInputError, match="unknown plan_id"):
        run(context(), include_planned_sorties_from="missing")
    for bad_slot, message in [
        (slot(tail_no="missing"), "unknown aircraft"),
        (slot(4, 2), "end after"),
        (slot(start=NOW.replace(tzinfo=None)), "timezone-aware"),
    ]:
        plan = PlanDraft(plan_id="P1", scenario_time=NOW, sortie_slots=[bad_slot])
        with pytest.raises(ToolInputError, match=message):
            run(context(plan=plan), include_planned_sorties_from="P1")
    with pytest.raises(ToolInputError, match="timezone-aware"):
        run(replace(context(), scenario_time=NOW.replace(tzinfo=None)))
    with pytest.raises(ToolInputError, match="later than scenario_time"):
        run(context([airframe(updated_at=NOW + timedelta(hours=1))]))


@pytest.mark.parametrize(
    "bad_model", [Classifier(float("nan")), Classifier(1.5), object()]
)
def test_bad_models_fall_back_without_leaking_invalid_probabilities(bad_model):
    model = artifact(models={24: bad_model, 48: Classifier(0.5), 72: Classifier(0.25)})
    output = run(context(models=Models(model)))
    assert output.model_version == BASELINE_VERSION
    assert output.per_aircraft[0].prob_by_horizon == {"24": 1, "48": 1, "72": 1}


def test_reversed_model_class_order_selects_fmc_class():
    model = Classifier(0.75)
    model.classes_ = np.array([1, 0])
    output = run(context(models=Models(artifact(models={24: model}))), horizons_h=[24])
    assert output.per_aircraft[0].prob_by_horizon == {"24": 0.25}


@pytest.mark.parametrize("error", [NotImplementedError(), OSError(), ImportError()])
def test_unavailable_store_falls_back(error):
    assert run(context(models=Models(error=error))).model_version == BASELINE_VERSION


def test_explanation_failure_preserves_valid_model_predictions():
    model = artifact(explainers={24: object()})
    output = run(context(models=Models(model)))
    assert output.model_version == model.model_version
    assert output.per_aircraft[0].prob_by_horizon["24"] == 0.75
    assert any("Baseline:" in driver for driver in output.per_aircraft[0].top_drivers)


@pytest.mark.parametrize(
    "updates",
    [
        {"model_version": ""},
        {"model_version": 7},
        {"feature_names": ("unknown_column",)},
        {"feature_names": ("type", "type")},
        {"calibrators": {24: Classifier(0.5)}},
    ],
)
def test_invalid_artifact_metadata_and_calibrators_fall_back(updates):
    output = run(context(models=Models(artifact(**updates))))
    assert output.model_version == BASELINE_VERSION
    assert output.per_aircraft[0].prob_by_horizon == {"24": 1, "48": 1, "72": 1}


@pytest.mark.parametrize(
    "probabilities", [[], [0, 0], [1, 1], [0.2, 0.7, 0.9], [0, 1, 0.5, 0.5]]
)
def test_poisson_binomial_quantiles_against_enumerated_outcomes(probabilities):
    mass = [0.0] * (len(probabilities) + 1)
    for outcome in product([0, 1], repeat=len(probabilities)):
        chance = math.prod(
            p if value else 1 - p for value, p in zip(outcome, probabilities)
        )
        mass[sum(outcome)] += chance

    def quantile(level):
        return next(
            count
            for count in range(len(mass))
            if math.fsum(mass[: count + 1]) >= level - 1e-12
        )

    output = fleet_forecast(probabilities)
    assert output.mean == pytest.approx(sum(probabilities))
    assert output.p10 == quantile(0.1)
    assert output.p90 == quantile(0.9)


def test_real_seeded_scenario_and_future_scripted_events():
    path = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
    scenario = build_scenario(load_config(path), 42)
    ctx = replace(
        context(),
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        scenario_time=min(item.updated_at for item in scenario.aircraft),
    )
    before = scenario.model_dump_json()
    output = run(ctx)
    assert len(output.per_aircraft) == 150
    assert output == run(ctx)
    assert scenario.model_dump_json() == before
    for key, summary in output.fleet.items():
        base, type_name = key.split("/", 1)
        tails = {
            item.tail_no
            for item in scenario.aircraft
            if item.base_id == base and item.type == type_name
        }
        assert summary.mean == sum(
            item.prob_by_horizon["24"]
            for item in output.per_aircraft
            if item.tail in tails
        )


def test_real_fitted_classifier_and_isotonic_calibration():
    pd = pytest.importorskip("pandas")
    ensemble = pytest.importorskip("sklearn.ensemble")
    isotonic = pytest.importorskip("sklearn.isotonic")
    names = ("hours_to_next_inspection", "flight_hours_total")
    training = pd.DataFrame(
        {
            names[0]: [1, 5, 10, 40, 70, 90],
            names[1]: [3000, 2000, 1000, 3000, 2000, 1000],
        }
    )
    labels = [0, 0, 0, 1, 1, 1]
    model = ensemble.GradientBoostingClassifier(
        n_estimators=5, max_depth=1, random_state=42
    ).fit(training, labels)
    calibration = isotonic.IsotonicRegression(out_of_bounds="clip").fit(
        model.predict_proba(training)[:, 1], labels
    )
    fitted = artifact(
        models={24: model},
        calibrators={24: calibration},
        feature_names=names,
        explainers={24: object()},
    )
    output = run(context(models=Models(fitted)), horizons_h=[24])
    assert output.model_version == fitted.model_version
    assert output.per_aircraft[0].prob_by_horizon["24"] == 1.0
