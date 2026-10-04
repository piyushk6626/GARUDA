"""Public contract, CP-SAT constraints, independent checking, and draft writes."""

import asyncio
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace

import pytest

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import Assignment, FlightLeg, PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.deconflict_airspace.explain import check_conflicts, explain
from garuda.optimization.deconflict_airspace.model import AirspaceSolution, solve
from garuda.optimization.deconflict_airspace.problem import build_problem
from garuda.optimization.deconflict_airspace.schemas import (
    Conflict, DeconflictAirspaceInput, DeconflictAirspaceOutput, FlightAdjustment, SeparationRules,
)
from garuda.optimization.deconflict_airspace.tool import SPEC, deconflict_airspace
from garuda.schemas.adsb import AdsbTrack
from garuda.schemas.airspace import AirspaceZone

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def at(minutes):
    return NOW + timedelta(minutes=minutes)


def zone(**updates):
    data = dict(zone_id="Z1", name="Test zone", type="CTR",
                wkt="POLYGON((75 28,75.2 28,75.2 28.2,75 28.2,75 28))",
                floor_ft=10000, ceiling_ft=40000, controlling_authority="ATC", source="synthetic")
    data.update(updates)
    return AirspaceZone(**data)


def civil(**updates):
    data = dict(time=NOW, source_id="test", icao24="abc123", callsign="CIV1", origin_country="India",
                last_contact=NOW, lat=28.1, lon=75.1, alt_m=18000 * .3048,
                speed_ms=0, heading=90, vrate=0, on_ground=False, position_source=0)
    data.update(updates)
    return AdsbTrack(**data)


class Plans:
    def __init__(self, plan):
        self.plan, self.writes = plan, []

    def get(self, plan_id):
        if self.plan.plan_id != plan_id:
            raise KeyError(plan_id)
        return self.plan

    def put(self, plan):
        self.plan = plan
        self.writes.append(plan)
        return plan.plan_id


def context(count=2, zones=None, ceiling=18000, tracks=(), starts=None, missions=None,
            altitude=18000, duration=10):
    starts = starts or [30] * count
    mission_ids = missions or [f"M{i + 1}" for i in range(count)]
    assignments = [Assignment(mission_id=mission_ids[i], tail_no=f"T{i + 1}", config_id="C1",
                              depart=at(starts[i]), tot=at(starts[i] + duration / 2)) for i in range(count)]
    legs = [FlightLeg(mission_id=a.mission_id, tail_no=a.tail_no, waypoints=[
        Waypoint(lat=28.1, lon=75.02, alt_ft=altitude, eta=a.depart),
        Waypoint(lat=28.1, lon=75.18, alt_ft=altitude, eta=a.depart + timedelta(minutes=duration)),
    ]) for a in assignments]
    slots = [SortieSlot(slot_id=f"S{i}", mission_id=a.mission_id, tail_no=a.tail_no, role="PILOT",
                        aircraft_type="Su-30MKI", start=a.depart,
                        end=a.depart + timedelta(minutes=duration)) for i, a in enumerate(assignments)]
    plan = PlanDraft(plan_id="P1", scenario_time=NOW, assignments=assignments, legs=legs, sortie_slots=slots)
    scenario = SimpleNamespace(
        missions=[SimpleNamespace(mission_id=mid, priority=i % 5 + 1, window_start=NOW, window_end=at(120))
                  for i, mid in enumerate(dict.fromkeys(mission_ids))],
        aircraft=[SimpleNamespace(tail_no=a.tail_no, ceiling_ft=ceiling) for a in assignments],
        zones=[zone()] if zones is None else zones, adsb_tracks=list(tracks),
    )
    return ToolContext(scenario=SimpleNamespace(snapshot=lambda: scenario), plans=Plans(plan),
                       models=None, providers=None, scenario_time=NOW)


def run(ctx, **inputs):
    return asyncio.run(deconflict_airspace(DeconflictAirspaceInput(plan_id="P1", **inputs), ctx))


def test_contract_and_spec_are_unchanged():
    assert list(DeconflictAirspaceInput.model_fields) == ["plan_id", "max_shift_min", "separation", "include_civil"]
    assert DeconflictAirspaceInput(plan_id="P1").model_dump() == dict(
        plan_id="P1", max_shift_min=30, separation=dict(time_min=3, altitude_ft=1000, lateral_km=10.0),
        include_civil=True)
    assert list(DeconflictAirspaceOutput.model_fields) == [
        "status", "adjusted_flights", "remaining_conflicts", "total_shift_min"]
    assert list(FlightAdjustment.model_fields) == ["flight", "shift_min", "altitude_block"]
    assert list(Conflict.model_fields) == ["flight_a", "flight_b", "zone", "detail"]
    assert SPEC.side_effects == "draft" and SPEC.timeout_s == 30 and SPEC.name == "deconflict_airspace"


def test_time_shift_preserves_priority_and_updates_a_copy_consistently():
    ctx = context()
    original = ctx.plans.plan
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.remaining_conflicts == []
    assert output.total_shift_min == 13
    assert len(output.adjusted_flights) == 1
    change = output.adjusted_flights[0]
    assert change.flight == "M2" and abs(change.shift_min) == 13 and change.altitude_block == "FL180"
    draft = ctx.plans.plan
    assert draft is not original and original.assignments[1].depart == at(30)
    assert draft.assignments[0] == original.assignments[0]
    delta = timedelta(minutes=change.shift_min)
    assert draft.assignments[1].depart == original.assignments[1].depart + delta
    assert draft.assignments[1].tot == original.assignments[1].tot + delta
    assert draft.legs[1].waypoints[1].eta == original.legs[1].waypoints[1].eta + delta
    assert draft.sortie_slots[1].end == original.sortie_slots[1].end + delta
    assert draft.crew_assignments == original.crew_assignments and draft.kpis == original.kpis
    assert DeconflictAirspaceOutput.model_validate_json(output.model_dump_json()) == output
    assert run(ctx).adjusted_flights == []
    assert len(ctx.plans.writes) == 1


def test_altitude_separation_avoids_time_shift():
    ctx = context(ceiling=20000)
    output = run(ctx, max_shift_min=0)
    assert output.status == "OPTIMAL" and output.total_shift_min == 0
    assert len(output.adjusted_flights) == 1 and output.adjusted_flights[0].altitude_block == "FL190"
    assert sorted(leg.waypoints[0].alt_ft for leg in ctx.plans.plan.legs) == [18000, 19000]
    assert all(len({w.alt_ft for w in leg.waypoints}) == 1 for leg in ctx.plans.plan.legs)


def test_capacity_cannot_be_bypassed_using_altitude_separation():
    ctx = context(ceiling=20000, zones=[zone(capacity_per_hour=1)])
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.total_shift_min == 10
    assert ctx.plans.plan.assignments[0].depart == at(30)
    assert abs((ctx.plans.plan.assignments[1].depart - at(30)).total_seconds()) == 600
    assert len({leg.waypoints[0].alt_ft for leg in ctx.plans.plan.legs}) == 2


def test_infeasible_returns_diagnostics_and_does_not_write():
    ctx = context()
    original = ctx.plans.plan.model_dump()
    output = run(ctx, max_shift_min=0)
    assert output.status == "INFEASIBLE" and output.adjusted_flights == []
    assert output.total_shift_min == 0 and output.remaining_conflicts
    assert any(c.zone in {"Z1", "LATERAL"} for c in output.remaining_conflicts)
    assert all(c.detail.startswith("Minimal conflict set:") for c in output.remaining_conflicts)
    assert ctx.plans.writes == [] and ctx.plans.plan.model_dump() == original


@pytest.mark.parametrize("zone_type", ["RESTRICTED", "TRA"])
def test_activation_window_contains_the_entire_pass(zone_type):
    ctx = context(count=1, zones=[zone(type=zone_type, active_from=at(40), active_to=at(50))])
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.total_shift_min == 10
    assert output.adjusted_flights[0].shift_min == 10
    ctx = context(count=1, zones=[zone(type=zone_type, active_from=at(40), active_to=at(49))])
    assert run(ctx).status == "INFEASIBLE"
    assert ctx.plans.writes == []


@pytest.mark.parametrize("zone_type", ["PROHIBITED", "DANGER"])
def test_permanent_forbidden_zone_reports_reroute(zone_type):
    ctx = context(count=1, zones=[zone(type=zone_type)])
    output = run(ctx)
    assert output.status == "INFEASIBLE"
    assert any("reroute" in conflict.detail for conflict in output.remaining_conflicts)
    assert ctx.plans.writes == []


def test_scheduled_forbidden_zone_can_be_avoided_in_time():
    ctx = context(count=1, zones=[zone(type="PROHIBITED", active_from=at(30), active_to=at(40))])
    assert run(ctx).total_shift_min == 10


def test_zero_capacity_and_capacity_above_the_route():
    ctx = context(count=1, zones=[zone(capacity_per_hour=0)])
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(count=1, zones=[zone(floor_ft=19000, capacity_per_hour=0)])
    assert run(ctx).status == "OPTIMAL"


def test_capacity_is_only_active_inside_scheduled_window():
    ctx = context(ceiling=20000, zones=[zone(capacity_per_hour=0, active_from=at(60), active_to=at(70))])
    output = run(ctx, max_shift_min=0)
    assert output.status == "OPTIMAL" and output.total_shift_min == 0


def test_route_zone_annotations_do_not_replace_geometry():
    ctx = context(count=1, zones=[zone(type="PROHIBITED")])
    assert ctx.plans.plan.legs[0].zones == []
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(count=1, zones=[zone(type="PROHIBITED", wkt="POLYGON((80 30,81 30,81 31,80 31,80 30))")])
    ctx.plans.plan.legs[0].zones = ["Z1"]
    assert run(ctx).status == "OPTIMAL"


def test_actual_zone_clipping_not_whole_route_occupancy():
    ctx = context(zones=[zone(wkt="POLYGON((75.08 28,75.12 28,75.12 28.2,75.08 28.2,75.08 28))")])
    payload = DeconflictAirspaceInput(plan_id="P1", separation=SeparationRules(lateral_km=0))
    problem = build_problem(payload, ctx)
    assert len(problem.passes) == 2
    assert 149000 < problem.passes[0].end - problem.passes[0].start < 151000
    solution = solve(problem)
    assert sum(abs(s) for s in solution.shifts) == 6


def test_waypoint_capacity_is_not_double_counted():
    ctx = context(count=1, zones=[zone(capacity_per_hour=1)])
    ctx.plans.plan.legs[0].waypoints.insert(1, Waypoint(lat=28.1, lon=75.1, alt_ft=18000, eta=at(35)))
    problem = build_problem(DeconflictAirspaceInput(plan_id="P1"), ctx)
    assert len(problem.passes) == 1
    assert run(ctx).status == "OPTIMAL"


def test_corridor_width_and_lateral_separation_outside_zones():
    ctx = context(count=1, zones=[zone(type="CORRIDOR", capacity_per_hour=0,
                                     wkt="LINESTRING(75 28.1,75.2 28.1)", width_km=10)])
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(zones=[])
    output = run(ctx, max_shift_min=0)
    assert output.status == "INFEASIBLE" and any(c.zone == "LATERAL" for c in output.remaining_conflicts)
    ctx = context(zones=[])
    assert run(ctx, max_shift_min=0, separation=SeparationRules(lateral_km=0)).status == "OPTIMAL"


def test_civil_traffic_and_include_civil_toggle():
    ctx = context(count=1, tracks=[civil()], ceiling=20000)
    output = run(ctx, max_shift_min=0)
    assert output.status == "OPTIMAL" and output.adjusted_flights[0].altitude_block == "FL190"
    ctx = context(count=1, tracks=[civil()])
    output = run(ctx)
    assert output.status == "INFEASIBLE" and any(c.flight_b == "CIVIL:abc123" for c in output.remaining_conflicts)
    assert run(ctx, include_civil=False).adjusted_flights == []


def test_unknown_civil_altitude_and_ground_future_states():
    ctx = context(count=1, tracks=[civil(alt_m=None)], ceiling=40000)
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(count=1, tracks=[civil(), civil(time=at(1), last_contact=at(1), on_ground=True)])
    assert run(ctx).status == "INFEASIBLE"  # Future ground observation must not erase current airborne track.
    ctx = context(count=1, tracks=[civil(), civil(time=NOW + timedelta(seconds=1), on_ground=True)])
    ctx = ToolContext(scenario=ctx.scenario, plans=ctx.plans, models=None, providers=None,
                      scenario_time=NOW + timedelta(seconds=2))
    assert run(ctx).status == "OPTIMAL"


def test_civil_prediction_detects_motion_into_route():
    # Starts over 40 km west; observed heading reaches the route near departure.
    track = civil(lon=74.5, speed_ms=30, heading=90)
    ctx = context(count=1, tracks=[track], ceiling=20000)
    output = run(ctx, max_shift_min=0)
    assert output.status == "OPTIMAL" and output.adjusted_flights
    assert output.adjusted_flights[0].altitude_block == "FL190"


def test_same_mission_members_preserve_offsets_and_get_unique_labels():
    ctx = context(ceiling=20000, missions=["M1", "M1"])
    output = run(ctx)
    assert output.total_shift_min == 0 and output.adjusted_flights[0].flight in {"M1:T1", "M1:T2"}
    assert ctx.plans.plan.assignments[0].depart == ctx.plans.plan.assignments[1].depart
    ctx = context(missions=["M1", "M1"])
    assert run(ctx).status == "INFEASIBLE"


def test_mission_tot_windows_can_force_early_or_late_shifts():
    ctx = context(count=1)
    mission = ctx.scenario.snapshot().missions[0]
    mission.window_start, mission.window_end = at(40), at(50)
    assert run(ctx).adjusted_flights[0].shift_min == 5
    ctx = context(count=1)
    mission = ctx.scenario.snapshot().missions[0]
    mission.window_end = at(32)
    assert run(ctx).adjusted_flights[0].shift_min == -3


def test_ceiling_and_departure_limits_produce_infeasible_results():
    ctx = context(count=1, ceiling=17000)
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(count=1, starts=[-1])
    assert run(ctx, max_shift_min=0).status == "INFEASIBLE"


def test_mission_reserved_airspace():
    ctx = context(count=1, zones=[zone(type="CAP", mission_id="OTHER")])
    assert run(ctx).status == "INFEASIBLE"
    ctx = context(count=1, zones=[zone(type="CAP", mission_id="M1")])
    assert run(ctx).status == "OPTIMAL"


def test_invalid_inputs_leave_draft_untouched():
    for inputs in [dict(max_shift_min=-1), dict(separation=SeparationRules(time_min=-1)),
                   dict(separation=SeparationRules(altitude_ft=-1)),
                   dict(separation=SeparationRules(lateral_km=float("nan")))]:
        ctx = context(count=1)
        with pytest.raises(ToolInputError):
            run(ctx, **inputs)
        assert ctx.plans.writes == []
    ctx = context(count=1)
    with pytest.raises(ToolInputError, match="unknown plan_id"):
        asyncio.run(deconflict_airspace(DeconflictAirspaceInput(plan_id="missing"), ctx))


@pytest.mark.parametrize("case", ["route", "eta", "naive", "mission", "zone", "geometry", "status"])
def test_bad_plan_and_scenario_data_are_actionable(case):
    ctx = context(count=1)
    if case == "route":
        ctx.plans.plan.legs = []
    elif case == "eta":
        ctx.plans.plan.legs[0].waypoints[1].eta = at(30)
    elif case == "naive":
        ctx.plans.plan.assignments[0].tot = at(35).replace(tzinfo=None)
    elif case == "mission":
        ctx.scenario.snapshot().missions = []
    elif case == "zone":
        ctx.plans.plan.legs[0].zones = ["unknown"]
    elif case == "geometry":
        ctx.scenario.snapshot().zones[0].wkt = "not WKT"
    else:
        ctx.plans.plan.status = "APPROVED"
    with pytest.raises(ToolInputError):
        run(ctx)
    assert ctx.plans.writes == []


def test_empty_plan_returns_optimal_without_writing():
    ctx = context(count=0)
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.adjusted_flights == [] and output.remaining_conflicts == []
    assert ctx.plans.writes == []


def test_independent_checker_rejects_bad_solution():
    ctx = context()
    problem = build_problem(DeconflictAirspaceInput(plan_id="P1"), ctx)
    assert check_conflicts(problem, [0, 0], [180, 180])
    with pytest.raises(RuntimeError, match="independent validation"):
        explain(problem, AirspaceSolution("OPTIMAL", (0, 0), (180, 180)))


def test_solver_matches_small_exhaustive_time_oracle():
    ctx = context(starts=[30, 31], duration=1)
    payload = DeconflictAirspaceInput(plan_id="P1", max_shift_min=3)
    problem = build_problem(payload, ctx)
    valid = [(shifts, sum(abs(s) * f.weight for s, f in zip(shifts, problem.flights)))
             for shifts in product(range(-3, 4), repeat=2)
             if not check_conflicts(problem, shifts, [180, 180])]
    solution = solve(problem)
    assert solution.status == "OPTIMAL"
    assert sum(abs(s) * f.weight for s, f in zip(solution.shifts, problem.flights)) == min(c for _, c in valid)


def test_changed_plan_is_detected_before_write(monkeypatch):
    import garuda.optimization.deconflict_airspace.tool as tool
    ctx = context()
    original_solve = tool.solve

    def concurrent_change(problem):
        solution = original_solve(problem)
        ctx.plans.plan.status = "APPROVED"
        return solution

    monkeypatch.setattr(tool, "solve", concurrent_change)
    with pytest.raises(ToolInputError, match="plan changed"):
        run(ctx)
    assert ctx.plans.writes == []


def test_unknown_solver_result_is_not_saved(monkeypatch):
    import garuda.optimization.deconflict_airspace.tool as tool
    ctx = context()
    monkeypatch.setattr(tool, "solve", lambda _: AirspaceSolution("UNKNOWN"))
    output = run(ctx)
    assert output.status == "UNKNOWN" and output.remaining_conflicts and not output.adjusted_flights
    assert ctx.plans.writes == []


def test_same_tail_cannot_overlap_even_on_distant_routes():
    ctx = context(zones=[])
    ctx.plans.plan.assignments[1].tail_no = "T1"
    ctx.plans.plan.legs[1].tail_no = "T1"
    ctx.plans.plan.sortie_slots[1].tail_no = "T1"
    for waypoint in ctx.plans.plan.legs[1].waypoints:
        waypoint.lat += 2
    ctx.scenario.snapshot().aircraft = ctx.scenario.snapshot().aircraft[:1]
    # A partial route must not truncate the aircraft's scheduled sortie time.
    for slot in ctx.plans.plan.sortie_slots:
        slot.end = at(50)
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.total_shift_min == 20


def test_custom_separation_and_altitude_domain_boundaries():
    ctx = context(ceiling=20000)
    output = run(ctx, max_shift_min=0, separation=SeparationRules(altitude_ft=1501))
    assert output.status == "OPTIMAL" and output.adjusted_flights[0].altitude_block == "FL196"
    ctx = context(ceiling=19000)
    assert run(ctx, max_shift_min=0, separation=SeparationRules(altitude_ft=1501)).status == "INFEASIBLE"


def test_partial_activation_capacity_and_distinct_airspace():
    ctx = context(count=1, zones=[zone(capacity_per_hour=0, active_from=at(35), active_to=at(45))])
    output = run(ctx)
    assert output.status == "OPTIMAL" and output.adjusted_flights[0].shift_min == -5
    ctx = context()
    for waypoint in ctx.plans.plan.legs[1].waypoints:
        waypoint.lat += 2
    assert run(ctx, max_shift_min=0).adjusted_flights == []


def test_subminute_tot_window_and_formation_offsets():
    ctx = context(count=1)
    ctx.scenario.snapshot().missions[0].window_start = at(35.1)
    assert run(ctx).adjusted_flights[0].shift_min == 1
    ctx = context(missions=["M1", "M1"], starts=[30, 44])
    before = ctx.plans.plan.model_copy(deep=True)
    ctx.scenario.snapshot().missions[0].window_start = at(40)
    output = run(ctx)
    assert output.status == "OPTIMAL" and {a.shift_min for a in output.adjusted_flights} == {5}
    after = ctx.plans.plan
    assert after.assignments[1].depart - after.assignments[0].depart == \
        before.assignments[1].depart - before.assignments[0].depart


def test_route_reentry_and_altitude_outside_forbidden_zone():
    ctx = context(count=1, zones=[zone(capacity_per_hour=1)])
    ctx.plans.plan.legs[0].waypoints = [
        Waypoint(lat=28.1, lon=lon, alt_ft=18000, eta=at(time))
        for lon, time in [(75.02, 30), (75.4, 35), (75.02, 40)]
    ]
    problem = build_problem(DeconflictAirspaceInput(plan_id="P1"), ctx)
    assert len(problem.passes) == 2
    assert run(ctx).status == "OPTIMAL"
    ctx = context(count=1, ceiling=20000, zones=[zone(type="PROHIBITED", ceiling_ft=18500)])
    output = run(ctx, max_shift_min=0)
    assert output.status == "OPTIMAL" and output.adjusted_flights[0].altitude_block == "FL186"
