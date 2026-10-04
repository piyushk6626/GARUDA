import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("pulp")

from garuda.contracts.events import Event
from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import Assignment, FlightLeg, PlanDraft, PlanKPIs, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.retask_rolling_horizon.baseline import (
    baseline,
    objective,
    validate_selection,
)
from garuda.optimization.retask_rolling_horizon.explain import plan_diff
from garuda.optimization.retask_rolling_horizon.model import solve
from garuda.optimization.retask_rolling_horizon.problem import build_problem
from garuda.optimization.retask_rolling_horizon.schemas import (
    RetaskRollingHorizonInput,
    RetaskRollingHorizonOutput,
)
from garuda.optimization.retask_rolling_horizon.tool import SPEC, retask_rolling_horizon
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.crew import Crew, Currency
from garuda.schemas.gis import BaseRecord, TerrainMeta
from garuda.schemas.mission import GeneratedScenario, Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import (
    TARGET_CLASSES,
    Compatibility,
    LoadConfig,
    WeaponStock,
    WeaponType,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def mission(mid="M1", start=4, priority=3, value=30, **updates):
    data = {
        "mission_id": mid,
        "type": "DCA",
        "priority": priority,
        "value": value,
        "requested_by": "test",
        "window_start": NOW + timedelta(hours=start),
        "window_end": NOW + timedelta(hours=start, minutes=20),
        "duration_min": 10,
        "target_id": None,
        "area_wkt": None,
        "required_capabilities": ["BVR"],
        "min_aircraft": 1,
        "max_aircraft": 1,
        "required_effect": "DENY",
        "min_pk": 0.3,
    }
    data.update(updates)
    return Mission(**data)


def aircraft(tail="A1", base="B1", type="Rafale", capabilities=None):
    return Aircraft(
        tail_no=tail,
        type=type,
        sqn_id="S1",
        base_id=base,
        status="FMC",
        fuel_kg=8000,
        fuel_capacity_kg=10000,
        flight_hours_total=100,
        hours_to_next_inspection=100,
        inspection_cycle_hours=100,
        turnaround_min=60,
        capabilities=capabilities or ["BVR"],
        range_km=1000,
        cruise_kts=400,
        ceiling_ft=40000,
        updated_at=NOW,
    )


def crew(cid, base="B1", type="Rafale"):
    return Crew(
        crew_id=cid,
        callsign=cid,
        role="PILOT",
        sqn_id="S1",
        base_id=base,
        primary_type=type,
        qualifications={type: ["BVR", "AAR"]},
        currency=Currency(
            by_type={type: NOW - timedelta(days=1)}, aar=NOW - timedelta(days=1)
        ),
        flight_hours_total=100,
        flight_hours_last_30d=10,
        duty_hours_24h=0,
        rest_hours_since_last_duty=14,
        fatigue_score=0.1,
        medical_status="FIT",
        updated_at=NOW,
    )


class Plans:
    def __init__(self, plan):
        self.items = {plan.plan_id: plan}

    def get(self, plan_id):
        return self.items[plan_id]

    def put(self, plan):
        self.items[plan.plan_id] = plan
        return plan.plan_id


def context(missions=None, aircrafts=None, assignments=None):
    missions = missions or [mission()]
    aircrafts = aircrafts or [aircraft("A1"), aircraft("A2"), aircraft("A3", "B2")]
    assignments = (
        assignments
        if assignments is not None
        else [
            Assignment(
                tail_no="A1",
                config_id="C1",
                mission_id=missions[0].mission_id,
                depart=missions[0].window_start,
                tot=missions[0].window_start,
            )
        ]
    )
    bases = [
        BaseRecord(
            base_id=bid,
            name=bid,
            icao=bid,
            lat=20,
            lon=70,
            elevation_ft=0,
            runway_count=1,
            runway_length_m=2000,
            shelter_count=10,
            fuel_capacity_l=10000,
            ground_crew_teams=10,
            max_sorties_per_hour=10,
        )
        for bid in ("B1", "B2")
    ]
    scenario = GeneratedScenario(
        seed=42,
        missions=missions,
        targets=[],
        threats=[],
        tracks=[],
        weapons=[
            WeaponType(
                weapon_code="W",
                category="AAM_BVR",
                range_km=10,
                weight_kg=100,
                pk_by_target_class={tc: 0.5 for tc in TARGET_CLASSES},
                min_release_alt_ft=0,
                guidance="RADAR",
            )
        ],
        stocks=[
            WeaponStock(base_id=bid, weapon_code="W", quantity=100, updated_at=NOW)
            for bid in ("B1", "B2")
        ],
        load_configs=[
            LoadConfig(
                config_id="C1",
                aircraft_type="Rafale",
                role="DCA",
                stations={"S1": ["Wx1"]},
                total_weight_kg=100,
                range_penalty_pct=0,
            )
        ],
        compatibilities=[
            Compatibility(
                aircraft_type="Rafale", weapon_code="W", allowed_stations=["S1"]
            )
        ],
        cells=[],
        observations=[],
        bases=bases,
        features=[],
        borders=[],
        distances=[],
        terrain=TerrainMeta(
            lat_min=10,
            lat_max=30,
            lon_min=60,
            lon_max=80,
            cell_km=10,
            rows=2,
            cols=2,
            elev_min_m=0,
            elev_max_m=100,
        ),
        aircraft_types=[],
        squadrons=[],
        aircraft=aircrafts,
        aircraft_history=[],
        crew=[crew(f"{bid}-P{i}", bid) for bid in ("B1", "B2") for i in range(6)],
        crew_duty=[],
        zones=[],
        adsb_tracks=[],
    )
    plan = PlanDraft(
        plan_id="original",
        scenario_time=NOW,
        assignments=assignments,
        kpis=PlanKPIs(
            value=sum(
                m.value
                for m in missions
                if any(a.mission_id == m.mission_id for a in assignments)
            ),
            fulfilment_pct=100,
            sorties=len(assignments),
            risk=0,
        ),
    )
    providers = SimpleNamespace(
        availability=SimpleNamespace(p_fmc=lambda *args: 1.0),
        component_risk=SimpleNamespace(aircraft_risk=lambda *args: 0.0),
        route=SimpleNamespace(route=lambda origin, dest, *args: [origin, dest]),
        capacity=SimpleNamespace(cap=lambda *args: 10),
        pk=SimpleNamespace(pk=lambda *args: 0.8),
        weather_go=SimpleNamespace(p_go=lambda *args: 1.0),
    )
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: scenario),
        plans=Plans(plan),
        models=None,
        providers=providers,
        scenario_time=NOW,
    ), scenario


def request(type="aircraft_nmc", entity="A1", **updates):
    event_params = updates.pop("params", {})
    return RetaskRollingHorizonInput(
        plan_id="original",
        event=Event(type=type, entity_id=entity, params=event_params, time=NOW),
        **updates,
    )


def run(ctx, payload):
    return asyncio.run(retask_rolling_horizon(payload, ctx))


def test_frozen_public_contracts():
    assert list(RetaskRollingHorizonInput.model_fields) == [
        "plan_id",
        "event",
        "freeze_hours",
        "stability_weight",
        "mode",
        "time_limit_s",
    ]
    assert list(RetaskRollingHorizonOutput.model_fields) == [
        "new_plan_id",
        "diff",
        "missions_degraded",
        "kpi_delta",
        "status",
    ]
    assert SPEC.side_effects == "draft"
    assert RetaskRollingHorizonInput.model_fields["freeze_hours"].default == 2


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_nmc_replaces_aircraft_and_persists_child_without_mutation(mode):
    ctx, scenario = context()
    before = ctx.plans.get("original").model_dump()
    source = scenario.model_dump()
    output = run(ctx, request(mode=mode))
    assert output.status in {"FEASIBLE", "OPTIMAL"}
    child = ctx.plans.get(output.new_plan_id)
    assert child.parent_plan_id == "original" and child.status == "DRAFT"
    assert len(child.assignments) == 1 and child.assignments[0].tail_no != "A1"
    assert len(output.diff.changed) == 1 and not output.diff.removed
    assert output.missions_degraded == []
    assert output.kpi_delta["value"] == 0
    assert ctx.plans.get("original").model_dump() == before
    assert scenario.model_dump() == source


def test_frozen_affected_assignment_is_reported_infeasible_without_draft():
    ctx, _ = context(missions=[mission(start=1)])
    output = run(ctx, request())
    assert output.status == "INFEASIBLE" and output.new_plan_id == "original"
    assert output.missions_degraded == ["M1"] and output.diff == plan_diff([], [])
    assert len(ctx.plans.items) == 1


def test_freeze_boundary_and_whole_package_are_fixed():
    m = mission(start=2, min_aircraft=2, max_aircraft=2)
    assigned = [
        Assignment(
            tail_no=tail,
            config_id="C1",
            mission_id="M1",
            depart=m.window_start,
            tot=m.window_start,
        )
        for tail in ("A1", "A2")
    ]
    ctx, _ = context(missions=[m], assignments=assigned)
    problem = build_problem(request(entity="A3"), ctx)
    assert problem.frozen_missions == {"M1"} and problem.locked == assigned
    result = run(ctx, request(entity="A3"))
    assert ctx.plans.get(result.new_plan_id).assignments == assigned


def test_p1_request_preempts_low_priority_in_both_modes():
    low = mission("LOW", priority=5, value=10)
    high = mission("HIGH", priority=1, value=100)
    ctx, _ = context(missions=[low], aircrafts=[aircraft()])
    output = run(
        ctx,
        request("p1_request", "HIGH", params={"mission": high.model_dump(mode="json")}),
    )
    child = ctx.plans.get(output.new_plan_id)
    assert [a.mission_id for a in child.assignments] == ["HIGH"]
    assert output.missions_degraded == ["LOW"]
    assert [a.mission_id for a in output.diff.added] == ["HIGH"]
    assert [a.mission_id for a in output.diff.removed] == ["LOW"]
    assert output.kpi_delta["value"] == 90


def test_etr_restores_tail_for_later_launch():
    ctx, _ = context()
    output = run(ctx, request(params={"etr": (NOW + timedelta(hours=3)).isoformat()}))
    assert ctx.plans.get(output.new_plan_id).assignments[0].tail_no == "A1"
    assert not output.diff.changed


def test_base_closure_uses_aircraft_from_another_base():
    ctx, _ = context()
    result = run(ctx, request("base_closed", "B1"))
    assert ctx.plans.get(result.new_plan_id).assignments[0].tail_no == "A3"


def test_weather_override_drops_mission_with_no_go():
    ctx, _ = context(aircrafts=[aircraft()])
    result = run(ctx, request("weather_override", "M1", params={"p_go": 0}))
    child = ctx.plans.get(result.new_plan_id)
    assert not child.assignments and result.missions_degraded == ["M1"]
    assert child.unassigned[0].reason == "WEATHER"


def test_new_sam_updates_risk_and_invalidates_existing_route():
    ctx, _ = context()
    plan = ctx.plans.get("original")
    plan.legs = [
        FlightLeg(
            mission_id="M1",
            tail_no="A1",
            waypoints=[
                Waypoint(lat=20, lon=70, alt_ft=10000, eta=NOW + timedelta(hours=4))
            ],
        )
    ]
    sam = Threat(
        threat_id="SAM",
        type="SAM_MED",
        lon=70,
        lat=20,
        mobile=False,
        engagement_range_km=10,
        detection_range_km=20,
        min_alt_ft=0,
        max_alt_ft=50000,
        pk_base=0.8,
        confidence=1,
        first_seen=NOW,
        last_seen=NOW,
        status="ACTIVE",
    )
    result = run(
        ctx, request("new_sam", "SAM", params={"threat": sam.model_dump(mode="json")})
    )
    assert result.kpi_delta["risk"] > 0 and result.missions_degraded == ["M1"]
    assert not ctx.plans.get(result.new_plan_id).legs


def test_airborne_mission_is_locked_even_when_freeze_is_zero():
    m = mission(
        start=-0.5,
        window_end=NOW + timedelta(hours=1),
        duration_min=60,
        status="EXECUTING",
    )
    ctx, _ = context(missions=[m])
    result = run(ctx, request(freeze_hours=0))
    assert result.status == "INFEASIBLE" and result.new_plan_id == "original"


@pytest.mark.parametrize("setting", ["stock", "crew", "capacity"])
def test_resource_shortage_cannot_produce_false_feasible_assignments(setting):
    ctx, scenario = context()
    if setting == "stock":
        scenario.stocks = [
            s.model_copy(update={"reserved_qty": 100}) for s in scenario.stocks
        ]
    elif setting == "crew":
        scenario.crew = []
    else:
        ctx.providers.capacity.cap = lambda *args: 0
    result = run(ctx, request())
    assert not ctx.plans.get(result.new_plan_id).assignments
    assert result.missions_degraded == ["M1"]


def test_dependency_is_dropped_with_unavailable_predecessor():
    first, second = (
        mission("PRE", start=3),
        mission("DEP", start=4, dependencies=["PRE"]),
    )
    assignments = [
        Assignment(
            tail_no="A1",
            config_id="C1",
            mission_id="PRE",
            depart=first.window_start,
            tot=first.window_start,
        ),
        Assignment(
            tail_no="A2",
            config_id="C1",
            mission_id="DEP",
            depart=second.window_start,
            tot=second.window_start,
        ),
    ]
    ctx, _ = context(missions=[first, second], assignments=assignments)
    result = run(ctx, request("weather_override", "PRE", params={"p_go": 0}))
    assert not ctx.plans.get(result.new_plan_id).assignments
    assert set(result.missions_degraded) == {"PRE", "DEP"}


def test_historical_assignments_are_preserved_and_not_retasked():
    past = mission("PAST", start=-4, status="DONE")
    ctx, _ = context(missions=[past])
    result = run(ctx, request())
    assert (
        ctx.plans.get(result.new_plan_id).assignments
        == ctx.plans.get("original").assignments
    )
    assert result.missions_degraded == []


def test_tanker_loss_replaces_support_and_preserves_receiver_when_possible():
    receiver = mission()
    aar = mission(
        "AAR",
        type="AAR",
        start=3,
        window_end=NOW + timedelta(hours=5),
        duration_min=120,
        required_capabilities=["AAR"],
    )
    assignments = [
        Assignment(
            tail_no="A1",
            config_id="C1",
            mission_id="M1",
            depart=receiver.window_start,
            tot=receiver.window_start,
        ),
        Assignment(
            tail_no="T1",
            config_id="TANKER-CLEAN",
            mission_id="AAR",
            depart=aar.window_start,
            tot=aar.window_start,
        ),
    ]
    ctx, scenario = context(
        missions=[receiver, aar],
        aircrafts=[
            aircraft(),
            aircraft("T1", type="IL-78", capabilities=["AAR"]),
            aircraft("T2", type="IL-78", capabilities=["AAR"]),
        ],
        assignments=assignments,
    )
    scenario.crew += [crew(f"T-P{i}", type="IL-78") for i in range(4)]
    ctx.plans.get("original").legs = [
        FlightLeg(
            mission_id="M1",
            tail_no="A1",
            waypoints=[],
            needs_aar=True,
            fuel_demand_kg=500,
        )
    ]
    result = run(ctx, request("tanker_lost", "T1"))
    child = ctx.plans.get(result.new_plan_id)
    assert {(a.mission_id, a.tail_no) for a in child.assignments} == {
        ("M1", "A1"),
        ("AAR", "T2"),
    }
    assert not result.missions_degraded
    scenario.aircraft = [a for a in scenario.aircraft if a.tail_no != "T2"]
    dropped = run(ctx, request("tanker_lost", "T1"))
    assert not ctx.plans.get(dropped.new_plan_id).assignments
    assert set(dropped.missions_degraded) == {"M1", "AAR"}


@pytest.mark.parametrize(
    "updates",
    [{"freeze_hours": -1}, {"stability_weight": float("nan")}, {"time_limit_s": 0}],
)
def test_bad_solver_options_are_actionable(updates):
    ctx, _ = context()
    with pytest.raises(ToolInputError):
        run(ctx, request(**updates))


def test_unknown_plan_and_entities_are_actionable():
    ctx, _ = context()
    with pytest.raises(ToolInputError, match="known aircraft"):
        run(ctx, request(entity="missing"))
    with pytest.raises(ToolInputError, match="Unknown plan"):
        run(ctx, request().model_copy(update={"plan_id": "missing"}))


def test_diff_matches_exact_assignments_before_pairing_changes():
    a = Assignment(tail_no="A1", config_id="C1", mission_id="M", depart=NOW, tot=NOW)
    b = a.model_copy(update={"tail_no": "A2"})
    c = a.model_copy(update={"tot": NOW + timedelta(hours=1)})
    diff = plan_diff([a, b], [b, c])
    assert diff.changed == [(a, c)] and not diff.added and not diff.removed


def test_full_solver_respects_simultaneous_package_size():
    ctx, _ = context(missions=[mission(min_aircraft=2, max_aircraft=2)])
    result = run(ctx, request(mode="full"))
    assigned = ctx.plans.get(result.new_plan_id).assignments
    assert len(assigned) == 2 and len({a.tail_no for a in assigned}) == 2
    assert len({a.tot for a in assigned}) == 1


def test_full_and_fast_are_feasible_and_outperform_greedy():
    ctx, _ = context(missions=[mission(), mission("M2", value=20)])
    problem = build_problem(request(mode="full"), ctx)
    greedy = baseline(problem)
    full = solve(problem)
    problem.mode = "fast"
    fast = solve(problem)
    for result in (greedy, full, fast):
        assert validate_selection(problem, result.selected) == []
    assert full.objective >= fast.objective - 1e-6
    assert full.objective >= greedy.objective - 1e-6
    assert full.objective == pytest.approx(objective(problem, full.selected))


def test_high_stability_weight_avoids_preemption():
    low, high = mission("LOW", value=10), mission("HIGH", priority=1, value=100)
    ctx, _ = context(missions=[low], aircrafts=[aircraft()])
    result = run(
        ctx,
        request(
            "p1_request",
            "HIGH",
            stability_weight=1000,
            params={"mission": high.model_dump(mode="json")},
        ),
    )
    assert [a.mission_id for a in ctx.plans.get(result.new_plan_id).assignments] == [
        "LOW"
    ]
    assert result.missions_degraded == ["HIGH"]


def test_changed_assignments_invalidate_downstream_records():
    ctx, _ = context()
    old = ctx.plans.get("original")
    old.sortie_slots = [
        SortieSlot(
            slot_id="SLOT",
            mission_id="M1",
            tail_no="A1",
            role="PILOT",
            aircraft_type="Rafale",
            start=NOW + timedelta(hours=4),
            end=NOW + timedelta(hours=5),
        )
    ]
    old.legs = [FlightLeg(mission_id="M1", tail_no="A1", waypoints=[])]
    result = run(ctx, request())
    child = ctx.plans.get(result.new_plan_id)
    assert not child.sortie_slots and not child.legs and not child.crew_assignments
    assert old.sortie_slots and old.legs


def test_no_incumbent_does_not_extract_fractional_or_seeded_values(monkeypatch):
    import pulp
    from garuda.optimization.retask_rolling_horizon.model import _attempt

    ctx, _ = context()
    problem = build_problem(request(mode="full"), ctx)

    def no_solution(lp, *args, **kwargs):
        for variable in lp.variables():
            variable.varValue = 0.5
        if hasattr(pulp, "LpSolveStatus"):
            return SimpleNamespace(
                status=pulp.LpSolveStatus.TimeLimit, has_solution=False
            )
        lp.status, lp.sol_status = 0, 0
        return 0

    monkeypatch.setattr(pulp.LpProblem, "solve", no_solution)
    attempted = _attempt(problem, None, 1)
    assert attempted.status == "ERROR" and not attempted.assignments
    result = solve(problem)
    assert result.status == "FEASIBLE_NOT_OPTIMAL"
    assert validate_selection(problem, result.selected) == []


def test_recent_history_reserves_aircraft_turnaround():
    past, future = mission("PAST", start=-0.5, status="DONE"), mission("NEXT", start=0)
    ctx, _ = context(missions=[past, future], aircrafts=[aircraft()])
    result = run(ctx, request("p1_request", "NEXT", mode="full"))
    child = ctx.plans.get(result.new_plan_id)
    assert [a.mission_id for a in child.assignments] == ["PAST"]
    assert result.missions_degraded == ["NEXT"]


def test_airborne_package_survives_expired_tot_window():
    airborne = mission(
        start=-0.5,
        window_end=NOW - timedelta(minutes=5),
        duration_min=20,
        status="EXECUTING",
    )
    ctx, _ = context(missions=[airborne])
    # A supplied sortie end keeps the aircraft airborne past the TOT window.
    ctx.plans.get("original").sortie_slots = [
        SortieSlot(
            slot_id="S",
            mission_id="M1",
            tail_no="A1",
            role="PILOT",
            aircraft_type="Rafale",
            start=airborne.window_start,
            end=NOW + timedelta(hours=1),
        )
    ]
    result = run(ctx, request(entity="A2"))
    assert (
        ctx.plans.get(result.new_plan_id).assignments
        == ctx.plans.get("original").assignments
    )


def test_optional_graph_runs_real_tool():
    pytest.importorskip("langgraph")
    from garuda.optimization.retask_rolling_horizon.graph import GRAPH

    ctx, _ = context()
    result = asyncio.run(
        GRAPH.ainvoke(
            {"payload": request().model_dump(mode="json")},
            {"configurable": {"ctx": ctx}},
        )
    )
    output = RetaskRollingHorizonOutput.model_validate(result["output"])
    assert ctx.plans.get(output.new_plan_id).assignments[0].tail_no != "A1"
    assert output.diff.changed


def test_historical_risk_is_preserved_in_kpi_delta():
    past, active = mission("PAST", start=-4, status="DONE"), mission("ACTIVE")
    assignments = [
        Assignment(
            tail_no="A1",
            config_id="C1",
            mission_id="PAST",
            depart=past.window_start,
            tot=past.window_start,
        ),
        Assignment(
            tail_no="A2",
            config_id="C1",
            mission_id="ACTIVE",
            depart=active.window_start,
            tot=active.window_start,
        ),
    ]
    ctx, _ = context(missions=[past, active], assignments=assignments)
    ctx.plans.get("original").kpis.risk = 0.6
    result = run(ctx, request(entity="A3"))
    assert result.kpi_delta["risk"] == 0
    assert ctx.plans.get(result.new_plan_id).kpis.risk == 0.6


def test_new_sam_already_in_snapshot_is_still_an_event_risk_increase():
    ctx, scenario = context()
    scenario.threats = [
        Threat(
            threat_id="SAM",
            type="SAM_MED",
            lon=70,
            lat=20,
            mobile=False,
            engagement_range_km=10,
            detection_range_km=20,
            min_alt_ft=0,
            max_alt_ft=50000,
            pk_base=0.8,
            confidence=1,
            first_seen=NOW,
            last_seen=NOW,
            status="ACTIVE",
        )
    ]
    result = run(ctx, request("new_sam", "SAM"))
    assert result.kpi_delta["risk"] == pytest.approx(0.8)
