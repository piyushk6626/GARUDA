"""In-memory context ports used by the local API."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from garuda.api.context import build_context
from garuda.context.memory import InMemoryPlanStore, InMemoryScenarioSource, LocalModelStore
from garuda.contracts.plan import PlanDraft

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


def test_scenario_source_exposes_the_snapshot_and_bases():
    scenario = SimpleNamespace(bases=[SimpleNamespace(base_id="B1")], cells=[])
    source = InMemoryScenarioSource(scenario, as_of=WHEN)

    assert source.snapshot() is scenario
    assert source.base_ids() == ["B1"]
    assert source.data_as_of() == WHEN


def test_plan_store_round_trip():
    store = InMemoryPlanStore()
    plan = PlanDraft(plan_id="p1", scenario_time=WHEN)

    assert store.put(plan) == "p1"
    assert store.get("p1").plan_id == "p1"
    with pytest.raises(KeyError, match="unknown plan"):
        store.get("missing")


def test_model_names_reject_paths(tmp_path):
    store = LocalModelStore(tmp_path)

    with pytest.raises(ValueError, match="invalid model name"):
        store.load("../secret")


def test_model_store_round_trip(tmp_path):
    pytest.importorskip("joblib")
    store = LocalModelStore(tmp_path)

    assert store.save("weather", {"n": 1}, {"version": "v1"}) == "v1"
    assert store.load("weather") == {"n": 1}
    assert store.load("weather", "v1") == {"n": 1}


def test_unwired_provider_fails_with_its_own_error(tmp_path):
    ctx = build_context(SimpleNamespace(), scenario_time=WHEN, seed=1, model_dir=tmp_path)

    with pytest.raises(RuntimeError, match="provider fatigue.fatigue is not wired"):
        ctx.providers.fatigue.fatigue("c1", WHEN)
