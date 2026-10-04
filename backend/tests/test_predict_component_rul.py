import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from garuda.config import load_config
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.ml.predict_component_rul.predict import (
    COMPONENT_TYPES,
    ComponentRulArtifact,
    WeibullParameters,
    predict,
)
from garuda.ml.predict_component_rul.schemas import (
    ComponentRisk,
    MaintenanceAdvice,
    PredictComponentRulInput,
    PredictComponentRulOutput,
)
from garuda.ml.predict_component_rul.tool import SPEC, predict_component_rul


class Models:
    def __init__(self, artifact=None):
        self.artifact = artifact
        self.calls = []

    def load(self, name, version=None):
        self.calls.append(name)
        if isinstance(self.artifact, Exception):
            raise self.artifact
        return self.artifact

    def save(self, *args):
        pytest.fail("RUL inference must not write models")


def aircraft(tail="A1", age=120, cycle=800):
    return SimpleNamespace(
        tail_no=tail,
        inspection_cycle_hours=cycle,
        hours_to_next_inspection=cycle - age,
    )


def context(airframes=None, artifact=None):
    snapshot = SimpleNamespace(
        aircraft=[aircraft()] if airframes is None else airframes
    )
    calls = []

    def read():
        calls.append(True)
        return snapshot

    return ToolContext(
        scenario=SimpleNamespace(snapshot=read, calls=calls),
        plans=None,
        models=Models(artifact),
        providers=None,
        scenario_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )


def run(ctx=None, **payload):
    return asyncio.run(
        predict_component_rul(PredictComponentRulInput(**payload), ctx or context())
    )


def test_public_contract_and_spec_are_unchanged():
    assert PredictComponentRulInput().model_dump() == {
        "tail_nos": None,
        "sortie_hours_ahead": 6.0,
        "component_types": None,
    }
    assert list(PredictComponentRulOutput.model_fields) == [
        "items",
        "aircraft_risk",
        "recommended_maintenance",
    ]
    assert list(ComponentRisk.model_fields) == [
        "tail",
        "component",
        "hours_since_overhaul",
        "p_fail_next",
        "rul_p10",
        "rul_p50",
        "rul_p90",
    ]
    assert list(MaintenanceAdvice.model_fields) == ["tail", "component", "action"]
    assert SPEC.name == "predict_component_rul"
    assert SPEC.input_model is PredictComponentRulInput
    assert SPEC.output_model is PredictComponentRulOutput
    assert SPEC.side_effects == "none" and SPEC.timeout_s == 20


@pytest.mark.parametrize("age", [0, 120, 800, 1600])
def test_conditional_probability_and_inverse_quantiles(age):
    parameters = WeibullParameters()
    probability, *quantiles = predict(parameters, age, 6)
    survival = lambda t: math.exp(-((t / 800) ** 2.2))
    assert probability == pytest.approx(1 - survival(age + 6) / survival(age))
    assert 0 < quantiles[0] < quantiles[1] < quantiles[2]
    for level, remaining in zip([0.1, 0.5, 0.9], quantiles):
        assert 1 - survival(age + remaining) / survival(age) == pytest.approx(level)


def test_probabilities_increase_with_age_and_horizon_and_rul_decreases():
    values = [predict(WeibullParameters(), age, 6) for age in [0, 120, 800]]
    assert values[0][0] < values[1][0] < values[2][0]
    assert values[0][2] > values[1][2] > values[2][2]
    assert predict(WeibullParameters(), 120, 12)[0] > values[1][0]


@pytest.mark.parametrize("age", [5e-324, 1e-320, 1e-300, 100000, 1e100, 1e300])
def test_extreme_ages_do_not_underflow_survival_or_emit_nonfinite_values(age):
    values = predict(WeibullParameters(), age, 6)
    assert all(math.isfinite(value) for value in values)
    assert 0 <= values[0] <= 1
    assert 0 <= values[1] <= values[2] <= values[3]


def test_exponential_model_is_memoryless_and_matches_known_quantiles():
    for age in [0, 120, 100000]:
        probability, *quantiles = predict(
            WeibullParameters(shape=1, scale_hours=100), age, 6
        )
        assert probability == pytest.approx(-math.expm1(-0.06))
        assert quantiles == pytest.approx(
            [-100 * math.log1p(-p) for p in [0.1, 0.5, 0.9]]
        )


def test_seeded_failure_population_agrees_with_probability_and_rul_quantiles():
    # Draw unconditional Weibull lifetimes, then retain survivors at age 400.
    lifetimes = np.random.default_rng(42).weibull(2.2, 200000) * 800
    residuals = lifetimes[lifetimes > 400] - 400
    probability, *quantiles = predict(WeibullParameters(), 400, 100)
    assert np.mean(residuals <= 100) == pytest.approx(probability, abs=0.003)
    assert np.quantile(residuals, [0.1, 0.5, 0.9]) == pytest.approx(quantiles, rel=0.02)


def test_filtering_order_deduplication_and_independent_aircraft_risk():
    ctx = context([aircraft("B"), aircraft("A", age=300)])
    output = run(
        ctx, tail_nos=["B", "B"], component_types=["radar", "engine", "engine"]
    )
    assert [(item.tail, item.component) for item in output.items] == [
        ("B", "engine"),
        ("B", "radar"),
    ]
    assert all(item.hours_since_overhaul == 120 for item in output.items)
    assert output.aircraft_risk["B"] == pytest.approx(
        1 - math.prod(1 - item.p_fail_next for item in output.items)
    )
    assert ctx.models.calls == [SPEC.name] and ctx.scenario.calls == [True]
    assert output == run(ctx, tail_nos=["B"], component_types=["engine", "radar"])
    assert [(item.tail, item.component) for item in run(ctx).items] == [
        (tail, component) for tail in ["A", "B"] for component in COMPONENT_TYPES
    ]


@pytest.mark.parametrize(
    "payload", [{"tail_nos": []}, {"tail_nos": ["missing"]}, {"component_types": []}]
)
def test_empty_filters_do_not_load_models(payload):
    ctx = context(artifact=RuntimeError("must not load"))
    assert run(ctx, **payload).model_dump() == {
        "items": [],
        "aircraft_risk": {},
        "recommended_maintenance": [],
    }
    assert ctx.models.calls == []


def test_empty_fleet():
    assert run(context([])).items == []


def test_zero_horizon_has_zero_risk_and_valid_quantiles():
    output = run(sortie_hours_ahead=0)
    assert output.aircraft_risk == {"A1": 0}
    assert all(item.p_fail_next == 0 and item.rul_p10 > 0 for item in output.items)
    assert output.recommended_maintenance == []


@pytest.mark.parametrize("horizon", [-1, math.nan, math.inf, -math.inf])
def test_invalid_horizons_fail_before_reading_scenario(horizon):
    ctx = context()
    with pytest.raises(ToolInputError, match="sortie_hours_ahead"):
        run(ctx, sortie_hours_ahead=horizon)
    assert ctx.scenario.calls == [] and ctx.models.calls == []


def test_unknown_components_are_actionable_errors():
    with pytest.raises(ToolInputError, match="unknown component_types.*wings"):
        run(component_types=["wings"])


@pytest.mark.parametrize(
    "age,cycle", [(-1, 800), (801, 800), (1, math.nan), (1, math.inf), (0, 0)]
)
def test_invalid_inspection_records_are_rejected(age, cycle):
    with pytest.raises(ToolInputError, match="invalid inspection hours"):
        run(context([aircraft(age=age, cycle=cycle)]))


@pytest.mark.parametrize(
    "missing", [None, KeyError("missing"), FileNotFoundError(), NotImplementedError()]
)
def test_missing_models_use_documented_baseline(missing):
    assert run(context(artifact=missing)) == run()


def test_artifact_changes_component_parameters_without_changing_public_output():
    model = ComponentRulArtifact(
        {
            "engine": WeibullParameters(2.2, 200),
            "radar": WeibullParameters(1, 2000),
        }
    )
    output = run(context(artifact=model), component_types=["engine", "radar"])
    assert output.items[0].p_fail_next > output.items[1].p_fail_next
    assert output == PredictComponentRulOutput.model_validate_json(
        output.model_dump_json()
    )


@pytest.mark.parametrize(
    "bad_model",
    [
        object(),
        ComponentRulArtifact(None),
        ComponentRulArtifact({}),
        ComponentRulArtifact({"engine": object()}),
    ],
)
def test_invalid_artifacts_fail_without_silent_fallback(bad_model):
    with pytest.raises(ValueError, match="model"):
        run(context(artifact=bad_model), component_types=["engine"])


@pytest.mark.parametrize(
    "parameters",
    [
        {"shape": 0},
        {"shape": -2},
        {"shape": math.nan},
        {"scale_hours": 0},
        {"scale_hours": math.inf},
    ],
)
def test_invalid_weibull_parameters(parameters):
    with pytest.raises(ValueError, match="finite and positive"):
        WeibullParameters(**parameters)


def test_inspection_advice_on_probability_threshold_and_inspection_margin():
    assert run().recommended_maintenance == []
    due = run(context([aircraft(age=795)]), component_types=["engine"])
    assert due.items[0].p_fail_next < 0.1
    assert due.recommended_maintenance == [
        MaintenanceAdvice(tail="A1", component="engine", action="inspect")
    ]
    high = ComponentRulArtifact({"engine": WeibullParameters(1, 10)})
    risky = run(context(artifact=high), component_types=["engine"])
    assert risky.items[0].p_fail_next > 0.1 and len(risky.recommended_maintenance) == 1


def test_very_small_and_certain_aircraft_risks_are_preserved():
    model = ComponentRulArtifact({"engine": WeibullParameters(1, 1e20)})
    output = run(context(artifact=model), component_types=["engine"])
    assert 0 < output.aircraft_risk["A1"] < 1e-15
    assert run(sortie_hours_ahead=1e300).aircraft_risk == {"A1": 1.0}


def test_seeded_project_scenario_is_read_only_and_deterministic():
    config_path = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
    scenario = build_scenario(load_config(config_path), 42)
    before = scenario.model_dump_json()
    ctx = replace(context(), scenario=SimpleNamespace(snapshot=lambda: scenario))
    output = run(ctx)
    assert len(output.items) == len(scenario.aircraft) * 4
    assert len(output.aircraft_risk) == len(scenario.aircraft)
    assert output == run(ctx)
    assert scenario.model_dump_json() == before
