"""Weapon-target assignment: greedy, MILP, and scenario Pk adjustments."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.tool import ToolInputError
from garuda.optimization.assign_weapons_to_targets.baseline import baseline, local_search
from garuda.optimization.assign_weapons_to_targets.explain import explain
from garuda.optimization.assign_weapons_to_targets.model import solve
from garuda.optimization.assign_weapons_to_targets.problem import WTAProblem, build_problem
from garuda.optimization.assign_weapons_to_targets.schemas import AssignWeaponsToTargetsInput
from garuda.optimization.assign_weapons_to_targets.tool import assign_weapons_to_targets
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.gis import BaseRecord, BaseTargetDistance
from garuda.schemas.mission import Target
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import TARGET_CLASSES, LoadConfig, WeaponStock, WeaponType
from garuda.schemas.weather import WeatherCell

WHEN = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


def _problem(**overrides) -> WTAProblem:
    values = {
        "target_ids": ("T1", "T2"),
        "values": {"T1": 10.0, "T2": 10.0},
        "min_destroy_prob": {},
        "cost": {"AGM": 1.0},
        "stock": {("B1", "AGM"): 2},
        "pk": {("B1", "AGM", "T1"): 0.5, ("B1", "AGM", "T2"): 0.5},
    }
    values.update(overrides)
    return WTAProblem(**values)


def _pk_table(pk: float) -> dict[str, float]:
    return {target_class: pk for target_class in TARGET_CLASSES}


def _weapon(code: str, pk: float, guidance: str = "GPS", range_km: float = 500, category: str = "ASM") -> WeaponType:
    return WeaponType(
        weapon_code=code,
        category=category,
        range_km=range_km,
        weight_kg=400,
        pk_by_target_class=_pk_table(pk),
        min_release_alt_ft=200,
        guidance=guidance,
    )


def _target(target_id: str, value: float, hardness: float = 0.85, lat: float = 28.0, lon: float = 77.0) -> Target:
    return Target(
        target_id=target_id,
        target_class="RUNWAY",
        lon=lon,
        lat=lat,
        value=value,
        hardness=hardness,
        defended_by=[],
    )


def _stock(base_id: str, code: str, quantity: int, reserved: int = 0) -> WeaponStock:
    return WeaponStock(
        base_id=base_id,
        weapon_code=code,
        quantity=quantity,
        reserved_qty=reserved,
        updated_at=WHEN,
    )


class _Source:
    def __init__(self, scenario) -> None:
        self._scenario = scenario

    def snapshot(self):
        return self._scenario


def _ctx(scenario, seed: int = 1):
    return SimpleNamespace(scenario=_Source(scenario), seed=seed)


def _assigned(result) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in result.allocations:
        totals[row.target_id] = totals.get(row.target_id, 0) + row.count
    return totals


def test_greedy_spreads_shots_across_equal_targets() -> None:
    result = explain(_problem(), baseline(_problem()))
    assert _assigned(result) == {"T1": 1, "T2": 1}
    assert result.expected_destroyed_value == pytest.approx(10.0)
    assert result.kill_prob_by_target["T1"] == pytest.approx(0.5)
    assert result.stock_remaining == {"AGM": 0}


def test_greedy_prefers_the_higher_value_target() -> None:
    problem = _problem(
        values={"T1": 100.0, "T2": 1.0},
        stock={("B1", "AGM"): 1},
        pk={("B1", "AGM", "T1"): 0.8, ("B1", "AGM", "T2"): 0.8},
    )
    result = explain(problem, baseline(problem))
    assert _assigned(result) == {"T1": 1}
    assert result.expected_destroyed_value == pytest.approx(80.0)
    assert result.stock_remaining == {"AGM": 0}


def test_stock_and_shot_cap_are_respected() -> None:
    problem = _problem(
        target_ids=("T1",),
        values={"T1": 50.0},
        stock={("B1", "AGM"): 10},
        pk={("B1", "AGM", "T1"): 0.5},
        x_max=4,
    )
    result = explain(problem, baseline(problem))
    assert _assigned(result) == {"T1": 4}
    assert result.stock_remaining == {"AGM": 6}
    assert result.kill_prob_by_target["T1"] == pytest.approx(1 - 0.5**4)


def test_platform_capacity_limits_how_many_weapons_launch() -> None:
    problem = _problem(
        target_ids=("T1",),
        values={"T1": 20.0},
        stock={("B1", "AGM"): 5},
        pk={("B1", "AGM", "T1"): 0.6},
        platform_capacity={"B1": 1},
    )
    result = explain(problem, baseline(problem))
    assert _assigned(result) == {"T1": 1}
    assert result.stock_remaining == {"AGM": 4}


def test_coverage_threshold_is_filled_before_the_richer_target() -> None:
    problem = _problem(
        values={"T1": 1.0, "T2": 100.0},
        min_destroy_prob={"T1": 0.5},
        stock={("B1", "AGM"): 2},
    )
    result = explain(problem, baseline(problem))
    assert _assigned(result) == {"T1": 1, "T2": 1}
    assert result.kill_prob_by_target["T1"] == pytest.approx(0.5)


def test_local_search_unsplits_a_stacked_assignment() -> None:
    problem = _problem()
    improved = local_search(problem, {("B1", "AGM", "T1"): 2})
    assert improved == {("B1", "AGM", "T1"): 1, ("B1", "AGM", "T2"): 1}


def test_milp_matches_the_greedy_spread() -> None:
    problem = _problem()
    greedy = explain(problem, baseline(problem))
    exact = explain(problem, solve(problem))
    assert _assigned(exact) == _assigned(greedy)
    assert exact.expected_destroyed_value == pytest.approx(greedy.expected_destroyed_value)


def test_milp_keeps_the_best_effort_when_coverage_is_impossible() -> None:
    problem = _problem(
        target_ids=("T1",),
        values={"T1": 10.0},
        min_destroy_prob={"T1": 0.99},
        stock={("B1", "AGM"): 1},
        pk={("B1", "AGM", "T1"): 0.5},
    )
    result = explain(problem, solve(problem))
    assert _assigned(result) == {"T1": 1}
    assert result.kill_prob_by_target["T1"] == pytest.approx(0.5)


def test_milp_picks_the_higher_value_target() -> None:
    problem = _problem(
        values={"T1": 100.0, "T2": 1.0},
        stock={("B1", "AGM"): 1},
        pk={("B1", "AGM", "T1"): 0.8, ("B1", "AGM", "T2"): 0.8},
    )
    result = explain(problem, solve(problem))
    assert _assigned(result) == {"T1": 1}
    assert result.expected_destroyed_value == pytest.approx(80.0)


def test_build_problem_scales_pk_and_skips_unreachable_stock() -> None:
    near = _target("NEAR", 10, hardness=0.85, lat=28.0, lon=77.0)
    hard = _target("HARD", 10, hardness=0.95, lat=28.01, lon=77.01)
    far = _target("FAR", 10, hardness=0.85, lat=0.0, lon=0.0)
    cloudy = WeatherCell(
        time=WHEN,
        lat=28.0,
        lon=77.0,
        cloud_low_pct=90,
        cloud_mid_pct=10,
        cloud_high_pct=0,
        ceiling_ft=1500,
        visibility_km=3,
        wind_kts=5,
        wind_dir_deg=20,
        gust_kts=8,
        precip_mm_h=0,
        cape_jkg=100,
        temp_c=30,
        freezing_level_ft=15000,
        source="synthetic",
        issued_at=WHEN,
        flyability=0.5,
        cb_flag=False,
    )
    threat = Threat(
        threat_id="TH-1",
        type="SAM_MED",
        lon=77.0,
        lat=28.0,
        mobile=False,
        engagement_range_km=40,
        detection_range_km=80,
        min_alt_ft=0,
        max_alt_ft=60000,
        pk_base=0.8,
        confidence=1.0,
        first_seen=WHEN,
        last_seen=WHEN,
        status="ACTIVE",
    )
    scenario = SimpleNamespace(
        targets=[near, hard, far],
        weapons=[
            _weapon("LGB", 0.8, guidance="LASER", range_km=80),
            _weapon("ASM", 0.8, guidance="GPS", range_km=80),
            _weapon("POD-TGT", 0.3, guidance="LASER", category="PODS"),
        ],
        stocks=[
            _stock("B1", "LGB", 4, reserved=1),
            _stock("B1", "ASM", 2),
            _stock("B1", "POD-TGT", 3),
            _stock("B2", "ASM", 9),
        ],
        threats=[threat],
        cells=[cloudy],
        bases=[
            BaseRecord(
                base_id="B1",
                name="North",
                icao="VINO",
                lat=28.0,
                lon=77.0,
                elevation_ft=700,
                runway_count=1,
                runway_length_m=3000,
                shelter_count=4,
                fuel_capacity_l=1000,
                ground_crew_teams=2,
                max_sorties_per_hour=2,
            )
        ],
        distances=[BaseTargetDistance(base_id="B1", target_id="FAR", distance_km=2000)],
        aircraft=[],
        load_configs=[],
    )
    payload = AssignWeaponsToTargetsInput(
        target_ids=["NEAR", "HARD", "FAR"],
        base_ids=["B1"],
        scenario_time=WHEN,
    )
    problem = build_problem(payload, _ctx(scenario))

    assert ("B1", "POD-TGT") not in problem.stock
    assert problem.stock[("B1", "LGB")] == 3
    assert ("B2", "ASM") not in problem.stock
    assert ("B1", "LGB", "FAR") not in problem.pk
    assert problem.pk[("B1", "LGB", "HARD")] < problem.pk[("B1", "LGB", "NEAR")]
    assert problem.pk[("B1", "LGB", "NEAR")] < problem.pk[("B1", "ASM", "NEAR")]
    assert problem.pk[("B1", "ASM", "NEAR")] < 0.8


def test_platform_capacity_comes_from_flyable_jets() -> None:
    jet = Aircraft(
        tail_no="SU-1",
        type="Su-30MKI",
        sqn_id="SQ1",
        base_id="B1",
        status="FMC",
        fuel_kg=1000,
        fuel_capacity_kg=5000,
        flight_hours_total=100,
        hours_to_next_inspection=50,
        inspection_cycle_hours=100,
        turnaround_min=60,
        capabilities=["STRIKE"],
        range_km=1500,
        cruise_kts=450,
        ceiling_ft=50000,
        updated_at=WHEN,
    )
    load = LoadConfig(
        config_id="SU30-STRIKE-1",
        aircraft_type="Su-30MKI",
        role="STRIKE",
        stations={"S3": ["LGB-500x2"], "S5": ["ASM-MEDx1"]},
        total_weight_kg=1100,
        range_penalty_pct=4,
    )
    scenario = SimpleNamespace(
        targets=[_target("T1", 20)],
        weapons=[_weapon("AGM", 0.7, range_km=1000)],
        stocks=[_stock("B1", "AGM", 10)],
        threats=[],
        cells=[],
        bases=[],
        distances=[],
        aircraft=[jet],
        load_configs=[load],
    )
    payload = AssignWeaponsToTargetsInput(target_ids=["T1"], scenario_time=WHEN)
    problem = build_problem(payload, _ctx(scenario))
    assert problem.platform_capacity == {"B1": 3}
    result = explain(problem, baseline(problem))
    assert _assigned(result) == {"T1": 3}


def test_tool_returns_allocations_for_the_requested_targets() -> None:
    scenario = SimpleNamespace(
        targets=[_target("T1", 40, hardness=0.85), _target("T2", 5, hardness=0.85)],
        weapons=[_weapon("AGM", 0.6)],
        stocks=[_stock("B1", "AGM", 2)],
        threats=[],
        cells=[],
        bases=[],
        distances=[],
        aircraft=[],
        load_configs=[],
    )
    payload = AssignWeaponsToTargetsInput(target_ids=["T1", "T2"], scenario_time=WHEN)
    result = asyncio.run(assign_weapons_to_targets(payload, _ctx(scenario, seed=7)))
    assert _assigned(result)["T1"] >= 1
    assert set(result.kill_prob_by_target) == {"T1", "T2"}
    assert result.stock_remaining["AGM"] == 0


def test_unknown_target_is_an_actionable_error() -> None:
    scenario = SimpleNamespace(
        targets=[_target("T1", 1)],
        weapons=[],
        stocks=[],
        threats=[],
        cells=[],
        bases=[],
        distances=[],
        aircraft=[],
        load_configs=[],
    )
    payload = AssignWeaponsToTargetsInput(target_ids=["MISSING"], scenario_time=WHEN)
    with pytest.raises(ToolInputError, match="MISSING"):
        build_problem(payload, _ctx(scenario))
