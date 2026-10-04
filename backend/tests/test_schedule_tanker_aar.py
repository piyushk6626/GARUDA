import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("ortools")

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import FlightLeg, PlanDraft
from garuda.contracts.tool import ToolInputError
from garuda.optimization.schedule_tanker_aar.baseline import baseline
from garuda.optimization.schedule_tanker_aar.explain import explain
from garuda.optimization.schedule_tanker_aar.model import solve
from garuda.optimization.schedule_tanker_aar.problem import (
    SERVICE_MIN,
    Aarp,
    AarProblem,
    Demand,
    Tanker,
    Threat,
    build_problem,
    evaluate_route,
)
from garuda.optimization.schedule_tanker_aar.schemas import ScheduleTankerAarInput
from garuda.optimization.schedule_tanker_aar.tool import schedule_tanker_aar

T0 = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)


def tanker(tail: str = "IL-1", capacity: float = 10_000.0, lat: float = 28.0) -> Tanker:
    return Tanker(tail, lat, 77.0, capacity, 400.0, 420.0)


def demand(mission: str, fuel: float, start: float, end: float) -> Demand:
    return Demand(mission, f"SU-{mission}", fuel, start, end, 28.5, 77.5, (0,))


def toy(demands: list[Demand], tankers: list[Tanker] | None = None, **kwargs) -> AarProblem:
    return AarProblem(
        origin=T0,
        tankers=tankers or [tanker()],
        aarps=[Aarp("Z-AAR-01", 28.5, 77.5)],
        demands=demands,
        time_limit_s=1,
        **kwargs,
    )


def assert_feasible(problem: AarProblem, solution) -> None:
    for tanker_idx, route in enumerate(solution.routes):
        stops = [(s.demand_idx, s.aarp_idx) for s in route]
        assert evaluate_route(problem, tanker_idx, stops) is not None
        for stop in route:
            window = problem.demands[stop.demand_idx]
            assert window.start_min - 1e-6 <= stop.time_min <= window.end_min + 1e-6
        assert sum(s.offload_kg for s in route) <= problem.tankers[tanker_idx].capacity_kg


def test_both_events_served_with_spare_fuel() -> None:
    problem = toy([demand("A", 3000, 60, 90), demand("B", 2000, 120, 150)])
    solution = solve(problem)
    assert not solution.unserved
    assert_feasible(problem, solution)
    times = [s.time_min for s in solution.routes[0]]
    assert times[1] - times[0] >= SERVICE_MIN
    assert explain(problem, solution).spare_fuel_kg["IL-1"] == pytest.approx(5000.0)


def test_capacity_drops_smaller_event() -> None:
    problem = toy([demand("A", 7000, 60, 90), demand("B", 5000, 120, 150)])
    solution = solve(problem)
    assert [(problem.demands[i].mission_id, why) for i, why in solution.unserved] == [("B", "capacity")]
    assert_feasible(problem, solution)


def test_unreachable_window_is_unserved() -> None:
    problem = toy([demand("A", 1000, 0, 1)])
    solution = solve(problem)
    assert [why for _, why in solution.unserved] == ["window"]


def test_threat_arc_is_avoided() -> None:
    problem = toy([demand("A", 1000, 60, 200)], threats=[Threat(28.25, 77.25, 20.0)])
    assert evaluate_route(problem, 0, [(0, 0)]) is None
    solution = solve(problem)
    assert solution.unserved


def test_solver_not_worse_than_baseline() -> None:
    problem = toy(
        [demand("A", 4000, 60, 90), demand("B", 4000, 70, 100), demand("C", 4000, 130, 160)],
        [tanker("IL-1"), tanker("IL-2")],
    )
    assert len(solve(problem).unserved) <= len(baseline(problem).unserved)


def make_ctx(legs: list[FlightLeg]):
    aircraft = [
        SimpleNamespace(
            tail_no="IL-1", capabilities=["AAR_TANKER"], base_id="B1", fuel_kg=20_000.0,
            range_km=3500.0, cruise_kts=420.0, status="FMC",
        ),
        SimpleNamespace(
            tail_no="SU-1", capabilities=["BVR"], base_id="B1", fuel_kg=5000.0,
            range_km=2000.0, cruise_kts=470.0, status="FMC",
        ),
    ]
    scenario = SimpleNamespace(
        bases=[SimpleNamespace(base_id="B1", lat=28.0, lon=77.0)],
        aircraft=aircraft,
        zones=[SimpleNamespace(zone_id="Z-AAR-01", type="AAR_TRACK", wkt="LINESTRING (77.4 28.5, 77.6 28.5)")],
        threats=[],
    )
    plan = PlanDraft(plan_id="P1", scenario_time=T0, legs=legs)
    return SimpleNamespace(
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        plans=SimpleNamespace(get=lambda plan_id: plan),
        scenario_time=T0,
    )


def leg(mission: str, needs_aar: bool = True) -> FlightLeg:
    way = [
        Waypoint(lat=28.5, lon=77.4, alt_ft=25000, eta=T0 + timedelta(minutes=60)),
        Waypoint(lat=28.5, lon=77.6, alt_ft=25000, eta=T0 + timedelta(minutes=90)),
    ]
    return FlightLeg(mission_id=mission, tail_no=f"SU-{mission}", waypoints=way, needs_aar=needs_aar, fuel_demand_kg=3000.0)


def test_build_problem_creates_one_demand_per_aar_leg() -> None:
    ctx = make_ctx([leg("A"), leg("B", needs_aar=False)])
    problem = build_problem(ScheduleTankerAarInput(plan_id="P1"), ctx)
    assert [d.mission_id for d in problem.demands] == ["A"]
    assert [t.tail for t in problem.tankers] == ["IL-1"]
    assert problem.demands[0].end_min - problem.demands[0].start_min == pytest.approx(30.0)


def test_build_problem_rejects_unknown_ids() -> None:
    ctx = make_ctx([leg("A")])
    with pytest.raises(ToolInputError):
        build_problem(ScheduleTankerAarInput(plan_id="P1", tanker_tails=["SU-1"]), ctx)
    with pytest.raises(ToolInputError):
        build_problem(ScheduleTankerAarInput(plan_id="P1", candidate_aarp_ids=["nope"]), ctx)


def test_tool_end_to_end() -> None:
    ctx = make_ctx([leg("A")])
    out = asyncio.run(schedule_tanker_aar(ScheduleTankerAarInput(plan_id="P1", time_limit_s=1), ctx))
    assert out.kpis["served"] == 1.0
    assert out.tanker_routes[0].sequence[0].receiver == "SU-A"
    assert out.spare_fuel_kg["IL-1"] == pytest.approx(16_000.0 - 3000.0)
    assert not out.unserved
