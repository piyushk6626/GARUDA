import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("pulp")

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.assign_missions.baseline import baseline
from garuda.optimization.assign_missions.model import solve, solver_factory
from garuda.optimization.assign_missions.preprocess import build_problem
from garuda.optimization.assign_missions.schemas import (
    AssignMissionsInput,
    AssignMissionsOutput,
    ObjectiveWeights,
)
from garuda.optimization.assign_missions.tool import assign_missions
from garuda.optimization.assign_missions.validate import validate
from garuda.providers.protocols import Providers
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.crew import Crew, Currency
from garuda.schemas.gis import BaseRecord, TerrainMeta
from garuda.schemas.mission import GeneratedScenario, Mission, Target
from garuda.schemas.weapon import (
    TARGET_CLASSES,
    Compatibility,
    LoadConfig,
    WeaponStock,
    WeaponType,
)

T = datetime(2026, 10, 4, 4, tzinfo=timezone.utc)


def mission(mid="M1", *, hour=1, value=100, **changes):
    args = {
        "mission_id": mid,
        "type": "STRIKE",
        "priority": 1,
        "value": value,
        "requested_by": "test",
        "window_start": T + timedelta(hours=hour),
        "window_end": T + timedelta(hours=hour + 2),
        "duration_min": 30,
        "target_id": "target",
        "area_wkt": None,
        "required_capabilities": ["AG_PGM"],
        "min_aircraft": 1,
        "max_aircraft": 1,
        "required_effect": "DESTROY",
        "min_pk": 0.5,
    }
    args.update(changes)
    return Mission(**args)


def aircraft(tail="A1", **changes):
    args = {
        "tail_no": tail,
        "type": "Tejas",
        "sqn_id": "SQ",
        "base_id": "B1",
        "status": "FMC",
        "fuel_kg": 1000,
        "fuel_capacity_kg": 1000,
        "flight_hours_total": 100,
        "hours_to_next_inspection": 90,
        "inspection_cycle_hours": 100,
        "turnaround_min": 60,
        "capabilities": ["AG_PGM"],
        "range_km": 1000,
        "cruise_kts": 400,
        "ceiling_ft": 40000,
        "updated_at": T,
    }
    args.update(changes)
    return Aircraft(**args)


def crew(cid="C1", **changes):
    args = {
        "crew_id": cid,
        "callsign": cid,
        "role": "PILOT",
        "sqn_id": "SQ",
        "base_id": "B1",
        "primary_type": "Tejas",
        "qualifications": {"Tejas": ["AG"]},
        "currency": Currency(
            by_type={"Tejas": T - timedelta(days=1)}, night=T - timedelta(days=1)
        ),
        "flight_hours_total": 100,
        "flight_hours_last_30d": 10,
        "duty_hours_24h": 0,
        "rest_hours_since_last_duty": 24,
        "fatigue_score": 0.1,
        "medical_status": "FIT",
        "updated_at": T,
    }
    args.update(changes)
    return Crew(**args)


class Store:
    def __init__(self):
        self.items = {}

    def put(self, plan):
        self.items[plan.plan_id] = plan.model_copy(deep=True)
        return plan.plan_id

    def get(self, pid):
        return self.items[pid]


@pytest.fixture
def ctx():
    solver_factory(1, 42)  # Fail clearly if the opt extra has no working solver.
    scenario = GeneratedScenario(
        seed=42,
        missions=[mission()],
        targets=[
            Target(
                target_id="target",
                target_class="RADAR",
                lat=26.0,
                lon=73.1,
                value=1,
                hardness=0.1,
                defended_by=[],
            )
        ],
        aircraft=[aircraft()],
        crew=[crew()],
        bases=[
            BaseRecord(
                base_id="B1",
                name="base",
                icao="TEST",
                lat=26.0,
                lon=73.0,
                elevation_ft=10,
                runway_count=1,
                runway_length_m=3000,
                shelter_count=10,
                fuel_capacity_l=100000,
                ground_crew_teams=10,
                max_sorties_per_hour=10,
            )
        ],
        weapons=[
            WeaponType(
                weapon_code="BOMB",
                category="LGB",
                range_km=10,
                weight_kg=250,
                pk_by_target_class={k: 0.8 for k in TARGET_CLASSES},
                min_release_alt_ft=0,
                guidance="LASER",
            )
        ],
        load_configs=[
            LoadConfig(
                config_id="CFG",
                aircraft_type="Tejas",
                role="STRIKE",
                stations={"S1": ["BOMBx1"]},
                total_weight_kg=250,
                range_penalty_pct=0,
            )
        ],
        stocks=[
            WeaponStock(base_id="B1", weapon_code="BOMB", quantity=10, updated_at=T)
        ],
        compatibilities=[
            Compatibility(
                aircraft_type="Tejas", weapon_code="BOMB", allowed_stations=["S1"]
            )
        ],
        terrain=TerrainMeta(
            lat_min=0,
            lat_max=1,
            lon_min=0,
            lon_max=1,
            cell_km=1,
            rows=1,
            cols=1,
            elev_min_m=0,
            elev_max_m=1,
        ),
        threats=[],
        tracks=[],
        cells=[],
        observations=[],
        features=[],
        borders=[],
        distances=[],
        aircraft_types=[],
        squadrons=[],
        aircraft_history=[],
        crew_duty=[],
        zones=[],
        adsb_tracks=[],
    )
    source = SimpleNamespace(
        snapshot=lambda: scenario, base_ids=lambda: ["B1"], data_as_of=lambda: T
    )
    providers = Providers(
        availability=SimpleNamespace(p_fmc=lambda *args: 1.0),
        component_risk=SimpleNamespace(aircraft_risk=lambda *args: 0.1),
        weather_go=SimpleNamespace(p_go=lambda *args: 1.0),
        route=SimpleNamespace(route=lambda a, b, *args: [a, b]),
        capacity=SimpleNamespace(cap=lambda *args: 10),
        pk=SimpleNamespace(pk=lambda *args: 0.8),
        fatigue=SimpleNamespace(fatigue=lambda *args: 0.1, phi=lambda *args: 0.1),
        threat_risk=SimpleNamespace(raster=lambda *args: None),
    )
    return ToolContext(source, Store(), None, providers, T)


def run(ctx, **kwargs):
    return asyncio.run(
        assign_missions(AssignMissionsInput(scenario_time=T, **kwargs), ctx)
    )


def check(ctx, payload=None):
    p = build_problem(payload or AssignMissionsInput(scenario_time=T), ctx)
    result = solve(p)
    assert validate(p, result.selected, result.crew) == []
    greedy = baseline(p)
    if greedy is not None:
        assert validate(p, greedy.selected, greedy.crew) == []

        def objective(keys):
            completed = {p.candidates[k].assignment.mission_id for k in keys}
            return sum(p.missions[mid].value for mid in completed) + sum(
                p.effect_weight * p.candidates[k].pk
                - p.risk_weight * p.candidates[k].risk
                for k in keys
            )

        assert objective(result.selected) >= objective(greedy.selected) - 1e-5
    return p, result


def test_known_optimum_and_draft(ctx):
    ctx.scenario.snapshot().missions.append(mission("M2", value=60))
    p, solution = check(ctx)
    assert solution.status == "OPTIMAL"
    assert [p.candidates[k].assignment.mission_id for k in solution.selected] == ["M1"]
    output = run(ctx)
    assert output.kpis.value == 100 and output.kpis.fulfilment_pct == 0.5
    assert output.kpis.sorties == 1 and output.kpis.risk == pytest.approx(0.1)
    stored = ctx.plans.get(output.plan_id)
    assert stored.status == "DRAFT" and stored.assignments == output.assignments
    assert stored.scenario_time == T
    assert stored.crew_assignments == []  # schedule_crew owns crew assignment output.
    assert len(stored.sortie_slots) == 1
    assert stored.sortie_slots[0].aircraft_type == "Tejas"
    assert stored.sortie_slots[0].role == "PILOT"


def test_package_minimum_and_distinct_aircraft(ctx):
    s = ctx.scenario.snapshot()
    s.missions[0] = mission(min_aircraft=2, max_aircraft=2)
    assert run(ctx).assignments == []
    s.aircraft.append(aircraft("A2"))
    s.crew.append(crew("C2"))
    p, solution = check(ctx)
    assert len(solution.selected) == 2
    assert len({p.candidates[k].assignment.tail_no for k in solution.selected}) == 2


def test_optimizer_beats_greedy_for_competing_packages(ctx):
    s = ctx.scenario.snapshot()
    s.aircraft.append(aircraft("A2"))
    s.crew.append(crew("C2"))
    s.missions = [
        mission("BIG", value=100, min_aircraft=2, max_aircraft=2),
        mission("SMALL1", value=60),
        mission("SMALL2", value=60),
    ]
    p, sol = check(ctx)
    assert {p.candidates[k].assignment.mission_id for k in sol.selected} == {
        "SMALL1",
        "SMALL2",
    }
    assert {p.candidates[k].assignment.mission_id for k in baseline(p).selected} == {
        "BIG"
    }


def test_turnaround_blocks_flights_in_later_bucket(ctx):
    s = ctx.scenario.snapshot()
    s.crew.append(crew("C2"))
    s.missions = [
        mission("LONG", window_end=T + timedelta(hours=10), duration_min=400),
        mission("LATER", hour=7, value=60),
    ]
    p, sol = check(ctx)
    assert {p.candidates[k].assignment.mission_id for k in sol.selected} == {"LONG"}


def test_risk_weights_change_aircraft_choice(ctx):
    ctx.scenario.snapshot().aircraft.append(aircraft("A2"))
    ctx.providers.component_risk.aircraft_risk = lambda tail, hours: (
        0.9 if tail == "A1" else 0.1
    )
    output = run(ctx, weights=ObjectiveWeights(risk=10, effect=0))
    assert output.assignments[0].tail_no == "A2"


def test_maintenance_return_is_eligible_only_after_eta(ctx):
    s = ctx.scenario.snapshot()
    s.aircraft[0] = aircraft(
        status="NMC",
        status_reason="maintenance",
        maint_return_eta=T + timedelta(hours=2),
    )
    assert run(ctx).assignments[0].depart >= T + timedelta(hours=2)
    s.aircraft[0].maint_return_eta = T + timedelta(hours=4)
    assert not run(ctx).assignments


def test_reuse_aircraft_across_buckets_with_rested_crew(ctx):
    ctx.scenario.snapshot().missions.append(mission("M2", hour=19))
    _, solution = check(ctx)
    assert len(solution.selected) == 2


@pytest.mark.parametrize(
    "blocker,reason",
    [
        ("stock", "NO_WEAPON_STOCK"),
        ("weather", "WEATHER"),
        ("crew", "NO_QUALIFIED_CREW"),
        ("capacity", "BASE_CAPACITY"),
        ("range", "NO_TANKER"),
        ("availability", "NO_AIRCRAFT"),
        ("inspection", "NO_AIRCRAFT"),
        ("capability", "NO_AIRCRAFT"),
        ("pk", "NO_AIRCRAFT"),
    ],
)
def test_candidate_blockers(ctx, blocker, reason):
    s = ctx.scenario.snapshot()
    if blocker == "stock":
        s.stocks[0].reserved_qty = s.stocks[0].quantity
    elif blocker == "weather":
        ctx.providers.weather_go.p_go = lambda *args: 0.0
    elif blocker == "crew":
        s.crew[0].medical_status = "GROUNDED"
    elif blocker == "capacity":
        ctx.providers.capacity.cap = lambda *args: 0
    elif blocker == "range":
        s.aircraft[0].range_km = 1
    elif blocker == "availability":
        ctx.providers.availability.p_fmc = lambda *args: 0.0
    elif blocker == "inspection":
        s.aircraft[0].hours_to_next_inspection = 0.1
    elif blocker == "capability":
        s.aircraft[0].capabilities = ["BVR"]
    else:
        ctx.providers.pk.pk = lambda *args: 0.1
    output = run(ctx)
    assert not output.assignments
    assert output.status in {
        "OPTIMAL",
        "FEASIBLE",
    }  # Optional tasks: empty plan is feasible.
    assert output.unassigned[0].reason == reason


def test_stock_capacity_and_crew_are_shared(ctx):
    s = ctx.scenario.snapshot()
    s.aircraft.append(aircraft("A2"))
    s.missions.append(mission("M2", value=60))
    # Only one crew member can serve the two overlapping flights.
    _, sol = check(ctx)
    assert len(sol.selected) == 1
    s.crew.append(crew("C2"))
    s.stocks[0].quantity = 1
    _, sol = check(ctx)
    assert len(sol.selected) == 1 and "weapon_stock:B1:BOMB" in sol.binding
    s.stocks[0].quantity = 10
    ctx.providers.capacity.cap = lambda base, hour: 1 if hour == T else 0
    _, sol = check(ctx)
    assert len(sol.selected) == 1 and "base_capacity:B1:0" in sol.binding


def test_dependency_requires_fulfilment_and_prior_completion(ctx):
    s = ctx.scenario.snapshot()
    s.missions = [
        mission("PRE", value=10),
        mission("DEP", hour=19, dependencies=["PRE"]),
    ]
    _, sol = check(ctx)
    assert len(sol.selected) == 2
    s.missions[1] = mission("DEP", hour=1, dependencies=["PRE"])
    output = run(ctx)
    assert {a.mission_id for a in output.assignments} == {"PRE"}
    s.missions[0].status = "DONE"
    assert {a.mission_id for a in run(ctx).assignments} == {"DEP"}


def test_exact_locks_and_infeasible_conflict(ctx):
    s = ctx.scenario.snapshot()
    s.missions.append(mission("M2", value=60))
    p = build_problem(AssignMissionsInput(scenario_time=T), ctx)
    lock = next(
        c.assignment for c in p.candidates.values() if c.assignment.mission_id == "M2"
    )
    # Preserve a lock within the bucket rather than snapping to the grid.
    lock = lock.model_copy(
        update={
            "depart": lock.depart + timedelta(minutes=5),
            "tot": lock.tot + timedelta(minutes=5),
        }
    )
    output = run(ctx, locked_assignments=[lock])
    assert output.assignments == [lock]
    other = lock.model_copy(update={"mission_id": "M1"})
    before = len(ctx.plans.items)
    output = run(ctx, locked_assignments=[lock, other])
    assert output.status == "INFEASIBLE" and output.plan_id == ""
    assert not output.assignments and len(ctx.plans.items) == before
    invalid = lock.model_copy(update={"tot": lock.depart - timedelta(minutes=1)})
    assert run(ctx, locked_assignments=[invalid]).status == "INFEASIBLE"


@pytest.mark.parametrize("kind", ["AAR", "AIRLIFT", "SAR"])
def test_unsupported_roles_are_unassigned(ctx, kind):
    ctx.scenario.snapshot().missions = [
        mission(
            type=kind,
            target_id=None if kind == "AAR" else "target",
            area_wkt="POLYGON ((73 26, 73.1 26, 73.1 26.1, 73 26.1, 73 26))",
        )
    ]
    output = run(ctx)
    assert not output.assignments
    assert "No matching load configuration" in output.unassigned[0].detail


def test_filters_and_empty_selection(ctx):
    ctx.scenario.snapshot().missions.append(mission("M2", hour=19))
    assert {a.mission_id for a in run(ctx, mission_ids=["M2"]).assignments} == {"M2"}
    output = run(ctx, mission_ids=[])
    assert (
        output.assignments == [] and output.unassigned == [] and output.kpis.value == 0
    )
    with pytest.raises(ToolInputError, match="Unknown mission_ids"):
        run(ctx, mission_ids=["missing"])


def test_bad_inputs_and_invalid_forecasts(ctx):
    with pytest.raises(ToolInputError, match="positive"):
        run(ctx, horizon_hours=0)
    with pytest.raises(ToolInputError, match="finite"):
        run(ctx, weights=ObjectiveWeights(risk=float("nan")))
    with pytest.raises(ToolInputError, match="timezone-aware"):
        asyncio.run(
            assign_missions(
                AssignMissionsInput(scenario_time=T.replace(tzinfo=None)), ctx
            )
        )
    ctx.providers.availability.p_fmc = lambda *args: float("nan")
    with pytest.raises(ToolInputError, match="probability"):
        run(ctx)


def test_extension_hook_and_independent_validator(ctx):
    p = build_problem(AssignMissionsInput(scenario_time=T), ctx)
    seen = []

    def extension(model, x, problem):
        seen.append(problem)
        for var in x.values():
            model += var == 0

    p.extensions.append(extension)
    assert solve(p).selected == [] and seen == [p]
    p.extensions.clear()
    sol = solve(p)
    assert validate(p, sol.selected, {})
    p.stock["B1", "BOMB"] = 0
    assert any("Resource stock" in v for v in validate(p, sol.selected, sol.crew))


def test_two_seater_requires_squadron_wso_and_emits_seat_slots(ctx):
    s = ctx.scenario.snapshot()
    s.aircraft = [aircraft(type="Su-30MKI")]
    s.load_configs[0].aircraft_type = "Su-30MKI"
    s.compatibilities[0].aircraft_type = "Su-30MKI"
    currency = Currency(
        by_type={"Su-30MKI": T - timedelta(days=1)}, night=T - timedelta(days=1)
    )
    s.crew = [
        crew(
            primary_type="Su-30MKI",
            qualifications={"Su-30MKI": ["AG"]},
            currency=currency,
        ),
        crew(
            "WSO",
            primary_type="Su-30MKI",
            qualifications={"Su-30MKI": ["AG"]},
            currency=currency,
            role="WSO",
            sqn_id="OTHER",
        ),
    ]
    assert not run(ctx).assignments
    s.crew[1].sqn_id = "SQ"
    output = run(ctx)
    assert output.assignments
    assert {slot.role for slot in ctx.plans.get(output.plan_id).sortie_slots} == {
        "PILOT",
        "WSO",
    }


def test_reads_only_one_scenario_snapshot(ctx):
    s = ctx.scenario.snapshot()
    calls = []

    def snapshot():
        calls.append(1)
        return s

    ctx.scenario.snapshot = snapshot
    assert run(ctx).assignments
    assert calls == [1]


def test_no_route_is_not_replaced_with_an_assumed_route(ctx):
    ctx.providers.route.route = lambda *args: []
    output = run(ctx)
    assert not output.assignments
    assert "no feasible route" in output.unassigned[0].detail


def test_night_currency_is_checked_for_the_whole_sortie(ctx):
    s = ctx.scenario.snapshot()
    s.missions = [mission(hour=8, window_end=T + timedelta(hours=24), duration_min=800)]
    s.crew[0].currency.night = None
    output = run(ctx)
    assert not output.assignments
    assert output.unassigned[0].reason == "NO_QUALIFIED_CREW"


def test_cbc_fallback_when_highs_unavailable(ctx, monkeypatch):
    import pulp

    factory = getattr(pulp, "PULP_CBC_CMD", None) or pulp.COIN_CMD
    if not factory(msg=False).available():
        pytest.skip("CBC executable is not installed")
    monkeypatch.setattr(pulp.HiGHS, "available", lambda self: False)
    _p, sol = check(ctx)
    assert sol.status == "OPTIMAL" and sol.selected


def test_time_limit_without_incumbent_uses_valid_baseline(ctx, monkeypatch):
    import pulp

    monkeypatch.setattr(
        pulp.LpProblem,
        "solve",
        lambda *args: SimpleNamespace(status=-4, has_solution=False),
    )
    output = run(ctx)
    assert output.status == "FEASIBLE" and len(output.assignments) == 1


def test_time_limit_keeps_valid_incumbent(ctx, monkeypatch):
    import pulp

    original = pulp.LpProblem.solve

    def limited(model, *args, **kwargs):
        original(model, *args, **kwargs)
        return SimpleNamespace(status=-4, has_solution=True)

    monkeypatch.setattr(pulp.LpProblem, "solve", limited)
    output = run(ctx)
    assert output.status == "FEASIBLE" and output.assignments


def test_async_loop_remains_responsive(ctx, monkeypatch):
    import time

    import garuda.optimization.assign_missions.tool as module

    original = module._compute

    def delayed(*args):
        time.sleep(0.1)
        return original(*args)

    monkeypatch.setattr(module, "_compute", delayed)

    async def test():
        task = asyncio.create_task(
            assign_missions(AssignMissionsInput(scenario_time=T), ctx)
        )
        await asyncio.sleep(0.01)
        assert not task.done()
        return await task

    assert asyncio.run(test()).assignments


def test_contract_fields_and_defaults_are_unchanged():
    assert set(AssignMissionsInput.model_fields) == {
        "scenario_time",
        "horizon_hours",
        "mission_ids",
        "weights",
        "locked_assignments",
        "time_limit_s",
    }
    assert set(AssignMissionsOutput.model_fields) == {
        "plan_id",
        "status",
        "assignments",
        "unassigned",
        "kpis",
        "binding_constraints",
    }
    payload = AssignMissionsInput(scenario_time=T)
    assert payload.horizon_hours == 72 and payload.time_limit_s == 30
    assert payload.weights == ObjectiveWeights(risk=0.2, effect=0.3)


def test_seed42_fleet_solve_and_baseline(ctx):
    from pathlib import Path

    from garuda.config import load_config
    from garuda.generators.missions import build_scenario

    config = load_config(
        Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
    )
    scenario = build_scenario(config, 42)
    source = SimpleNamespace(snapshot=lambda: scenario)
    context = ToolContext(source, Store(), None, ctx.providers, config.start)
    p, solution = check(
        context, AssignMissionsInput(scenario_time=config.start, time_limit_s=10)
    )
    assert len(scenario.aircraft) == 150
    assert solution.selected
    assert all(
        p.missions[p.candidates[k].assignment.mission_id].type
        not in {"AAR", "SAR", "AIRLIFT"}
        for k in solution.selected
    )
