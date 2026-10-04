import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from types import SimpleNamespace

import pytest

pytest.importorskip("pulp")

from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.compute_sortie_capacity.explain import explain
from garuda.optimization.compute_sortie_capacity.model import (
    FAMILIES,
    build_model,
    solve,
)
from garuda.optimization.compute_sortie_capacity.problem import (
    AircraftGroup,
    BaseProblem,
    CapacityProblem,
    CrewResource,
    build_problem,
)
from garuda.optimization.compute_sortie_capacity.schemas import (
    ComputeSortieCapacityInput,
    ComputeSortieCapacityOutput,
)
from garuda.optimization.compute_sortie_capacity.tool import (
    SPEC,
    compute_sortie_capacity,
)
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.crew import Crew, Currency
from garuda.schemas.gis import BaseRecord, TerrainMeta
from garuda.schemas.mission import GeneratedScenario
from garuda.schemas.weapon import LoadConfig, WeaponStock

T = datetime(2026, 10, 4, 4, tzinfo=UTC)


def base_problem(**changes):
    group = AircraftGroup("Tejas", 1, 1000, (10,) * 4, ({"BOMB": 1},), {"PILOT": 1})
    base = BaseProblem(
        "B1",
        4,
        (group,),
        100,
        100000,
        {"BOMB": 100},
        200,
        100,
        (True,) * 4,
        tuple(
            CrewResource(f"C{i}", "PILOT", frozenset((0, p) for p in range(4)), 14)
            for i in range(100)
        ),
    )
    return replace(base, **changes)


def compute(base):
    problem = CapacityProblem((base,), 42)
    solution = solve(problem)
    return explain(problem, solution), solution.bases[base.base_id]


@pytest.mark.parametrize(
    "family,changes,total",
    [
        ("aircraft", {}, 40),
        ("ground_crew", {"ground_hours": 2}, 8),
        ("fuel", {"fuel_kg": 2000}, 2),
        ("munitions", {"stock": {"BOMB": 2}}, 2),
        ("runway", {"runway_ops": 4}, 8),
        (
            "crew",
            {
                "crew": (
                    CrewResource("C", "PILOT", frozenset((0, p) for p in range(4)), 14),
                )
            },
            4,
        ),
    ],
)
def test_known_optima_and_shadow_prices(family, changes, total):
    output, solution = compute(base_problem(**changes))
    assert output.totals["B1"] == total
    assert sum(solution.hourly) == pytest.approx(total)
    assert output.bottlenecks["B1"] == family
    assert output.shadow_prices["B1"][family] > 0
    assert set(output.shadow_prices["B1"]) == set(FAMILIES)
    assert all(isfinite(v) and v >= 0 for v in output.shadow_prices["B1"].values())


def test_runway_value_is_sorties_not_operations():
    output, _ = compute(base_problem(runway_ops=6))
    assert output.capacity == {"B1": [3] * 4}


def test_shadow_price_predicts_small_rhs_change():
    base = base_problem(ground_hours=2)
    _, original = compute(base)
    _, changed = compute(replace(base, ground_hours=2.1))
    assert sum(changed.hourly) - sum(original.hourly) == pytest.approx(
        4 * 0.1 * original.shadow_prices["ground_crew"]
    )


def test_aircraft_turnaround_spans_hours():
    base = base_problem()
    base = replace(
        base, groups=(replace(base.groups[0], turnaround_hours=2, available=(2,) * 4),)
    )
    _, solution = compute(base)
    assert sum(solution.hourly) == pytest.approx(4)
    assert all(
        sum(solution.hourly[max(0, p - 1) : p + 1]) <= 2 + 1e-6 for p in range(4)
    )


def test_subhour_turnaround_and_fractional_rounding(caplog):
    base = base_problem()
    base = replace(
        base,
        groups=(replace(base.groups[0], turnaround_hours=0.5, available=(1.25,) * 4),),
    )
    with caplog.at_level(logging.INFO):
        output, solution = compute(base)
    assert solution.hourly == pytest.approx([2.5] * 4)
    assert output.capacity == {"B1": [2] * 4}
    assert output.totals == {"B1": 8}
    assert "rounding loss 2.000000" in caplog.text


def test_shelter_limit():
    output, _ = compute(base_problem(shelters=2))
    assert output.totals == {"B1": 8}
    assert output.bottlenecks == {"B1": "aircraft"}


@pytest.mark.parametrize(
    "changes",
    [
        {"ground_hours": 0},
        {"fuel_kg": 0},
        {"stock": {}},
        {"crew": ()},
        {"shelters": 0},
    ],
)
def test_zero_resource_never_returns_stub(changes):
    output, _ = compute(base_problem(**changes))
    assert output.capacity == {"B1": [0] * 4}
    assert output.totals == {"B1": 0}


def test_weather_closure_and_reopening():
    output, _ = compute(base_problem(open_hours=(True, False, False, True)))
    assert output.capacity == {"B1": [10, 0, 0, 10]}
    output, _ = compute(base_problem(open_hours=(False,) * 4))
    assert output.capacity == {"B1": [0] * 4}
    assert output.bottlenecks == {"B1": "weather"}


def test_load_alternatives_share_aircraft_and_can_use_other_stock():
    base = base_problem(stock={"BOMB": 0, "MISSILE": 3})
    base = replace(
        base, groups=(replace(base.groups[0], loads=({"BOMB": 2}, {"MISSILE": 1})),)
    )
    output, _ = compute(base)
    assert output.totals == {"B1": 3}
    assert output.bottlenecks == {"B1": "munitions"}


def test_cross_qualified_crew_cannot_be_counted_twice():
    base = base_problem()
    groups = (base.groups[0], replace(base.groups[0], aircraft_type="Rafale"))
    member = CrewResource(
        "C", "PILOT", frozenset((g, p) for g in range(2) for p in range(4)), 14
    )
    output, _ = compute(replace(base, groups=groups, crew=(member,)))
    assert output.totals == {"B1": 4}


def test_multiseat_and_duty_constraints():
    base = base_problem()
    group = replace(base.groups[0], aircraft_type="IL-78", seats={"PILOT": 2})
    members = tuple(
        CrewResource(f"C{i}", "PILOT", frozenset((0, p) for p in range(4)), 2)
        for i in range(2)
    )
    output, solution = compute(replace(base, groups=(group,), crew=members))
    assert sum(solution.hourly) == pytest.approx(2)
    assert output.totals["B1"] <= 2


def test_independent_lp_feasibility_and_simple_greedy_bound():
    import pulp

    base = base_problem(ground_hours=3, runway_ops=10)
    model, hourly, _ = build_model(base)
    from garuda.optimization.compute_sortie_capacity.model import solver_factory

    model.solve(solver_factory(2, 42))
    values = [pulp.value(hourly[p]) for p in range(4)]
    assert model.valid(1e-6)
    # Independent feasible greedy plan: three sorties each hour.
    assert sum(values) >= 12
    assert all(v <= 3 + 1e-6 for v in values)
    assert sum(values) * 1000 <= base.fuel_kg
    assert sum(values) <= base.stock["BOMB"]


def test_integer_turnaround_matches_simpy():
    simpy = pytest.importorskip("simpy")
    base = base_problem()
    base = replace(
        base, groups=(replace(base.groups[0], turnaround_hours=2, available=(2,) * 4),)
    )
    env = simpy.Environment()
    launches = [0] * 4

    def aircraft_loop():
        while env.now < 4:
            launches[int(env.now)] += 1
            yield env.timeout(2)

    for _ in range(2):
        env.process(aircraft_loop())
    env.run(until=4)
    _, solution = compute(base)
    assert sum(solution.hourly) == sum(launches) == 4


def aircraft(**changes):
    return Aircraft(
        **(
            {
                "tail_no": "A1",
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
            | changes
        )
    )


def crew(**changes):
    return Crew(
        **(
            {
                "crew_id": "C1",
                "callsign": "C1",
                "role": "PILOT",
                "sqn_id": "SQ",
                "base_id": "B1",
                "primary_type": "Tejas",
                "qualifications": {"Tejas": ["AG"]},
                "currency": Currency(by_type={"Tejas": T - timedelta(days=1)}),
                "flight_hours_total": 100,
                "flight_hours_last_30d": 10,
                "duty_hours_24h": 0,
                "rest_hours_since_last_duty": 24,
                "fatigue_score": 0.1,
                "medical_status": "FIT",
                "updated_at": T,
            }
            | changes
        )
    )


@pytest.fixture
def ctx():
    scenario = GeneratedScenario(
        seed=42,
        aircraft=[aircraft()],
        crew=[crew()],
        bases=[
            BaseRecord(
                base_id="B1",
                name="base",
                icao="TEST",
                lat=26,
                lon=73,
                elevation_ft=10,
                runway_count=1,
                runway_length_m=3000,
                shelter_count=10,
                fuel_capacity_l=100000,
                ground_crew_teams=10,
                max_sorties_per_hour=10,
            )
        ],
        stocks=[
            WeaponStock(base_id="B1", weapon_code="BOMB", quantity=100, updated_at=T)
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
        targets=[],
        missions=[],
        threats=[],
        tracks=[],
        weapons=[],
        compatibilities=[],
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
        terrain=TerrainMeta(
            lat_min=20,
            lat_max=30,
            lon_min=70,
            lon_max=80,
            cell_km=10,
            rows=2,
            cols=2,
            elev_min_m=0,
            elev_max_m=10,
        ),
    )
    providers = SimpleNamespace(
        availability=SimpleNamespace(p_fmc=lambda tail, at: 1),
        weather_go=SimpleNamespace(p_go=lambda base, route, window, guidance: 1),
        fatigue=SimpleNamespace(fatigue=lambda cid, at: 0.1),
    )
    return ToolContext(
        SimpleNamespace(snapshot=lambda: scenario), None, None, providers, T
    )


def run(ctx, **changes):
    return asyncio.run(
        compute_sortie_capacity(ComputeSortieCapacityInput(start=T, **changes), ctx)
    )


def test_tool_contract_and_no_mutation(ctx):
    before = ctx.scenario.snapshot().model_dump(mode="json")
    output = run(ctx, horizon_hours=3)
    assert output.capacity == {"B1": [1, 1, 1]}
    assert output.totals == {"B1": 3}
    assert ctx.scenario.snapshot().model_dump(mode="json") == before
    assert set(ComputeSortieCapacityInput.model_fields) == {
        "base_ids",
        "start",
        "horizon_hours",
        "surge",
    }
    assert set(ComputeSortieCapacityOutput.model_fields) == {
        "capacity",
        "totals",
        "bottlenecks",
        "shadow_prices",
    }
    assert SPEC.input_model is ComputeSortieCapacityInput
    assert SPEC.output_model is ComputeSortieCapacityOutput
    assert SPEC.side_effects == "none"


def test_base_selection_empty_and_no_aircraft(ctx):
    assert run(ctx, base_ids=[]).model_dump() == {
        "capacity": {},
        "totals": {},
        "bottlenecks": {},
        "shadow_prices": {},
    }
    assert run(ctx, base_ids=["B1", "B1"], horizon_hours=2).totals == {"B1": 2}
    ctx.scenario.snapshot().aircraft.clear()
    assert run(ctx, horizon_hours=3).capacity == {"B1": [0, 0, 0]}


def test_independent_bases(ctx):
    scenario = ctx.scenario.snapshot()
    scenario.bases.append(scenario.bases[0].model_copy(update={"base_id": "B2"}))
    output = run(ctx, horizon_hours=3)
    assert output.capacity == {"B1": [1, 1, 1], "B2": [0, 0, 0]}
    assert run(ctx, base_ids=["B2"], horizon_hours=2).totals == {"B2": 0}


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"base_ids": ["missing"]}, "Unknown base_ids"),
        ({"horizon_hours": 0}, "horizon_hours must be positive"),
        ({"horizon_hours": -1}, "horizon_hours must be positive"),
    ],
)
def test_bad_inputs(ctx, changes, message):
    with pytest.raises(ToolInputError, match=message):
        run(ctx, **changes)


def test_naive_time(ctx):
    with pytest.raises(ToolInputError, match="timezone-aware"):
        asyncio.run(
            compute_sortie_capacity(
                ComputeSortieCapacityInput(start=T.replace(tzinfo=None)), ctx
            )
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, None])
def test_invalid_provider_probability(ctx, value):
    ctx.providers.availability.p_fmc = lambda tail, at: value
    with pytest.raises(ToolInputError, match="finite probability"):
        run(ctx, horizon_hours=1)


def test_forecast_hours_and_go_threshold(ctx):
    calls = []
    ctx.providers.availability.p_fmc = lambda tail, at: (
        calls.append(at) or (1 if at == T else 0)
    )
    assert run(ctx, horizon_hours=3).capacity == {"B1": [1, 0, 0]}
    assert calls == [T, T + timedelta(hours=1), T + timedelta(hours=2)]
    ctx.providers.availability.p_fmc = lambda tail, at: 1
    ctx.providers.weather_go.p_go = lambda base, route, window, guidance: (
        0.49 if window.start == T else 0.5
    )
    assert run(ctx, horizon_hours=2).capacity == {"B1": [0, 1]}


@pytest.mark.parametrize(
    "changes",
    [
        {"medical_status": "GROUNDED"},
        {"leave_until": T + timedelta(days=1)},
        {"current_assignment": "M1"},
        {"fatigue_score": 0.8},
        {"currency": Currency(by_type={"Tejas": T - timedelta(days=100)})},
    ],
)
def test_unavailable_crew(ctx, changes):
    ctx.scenario.snapshot().crew[:] = [crew(**changes)]
    assert run(ctx, horizon_hours=2).totals == {"B1": 0}


def test_forecast_fatigue_closes_crew_eligibility(ctx):
    ctx.providers.fatigue.fatigue = lambda cid, at: 0.8
    assert run(ctx, horizon_hours=2).totals == {"B1": 0}


def test_reserved_stock_and_load_token_aggregation(ctx):
    scenario = ctx.scenario.snapshot()
    scenario.stocks[0] = scenario.stocks[0].model_copy(
        update={"quantity": 10, "reserved_qty": 4}
    )
    scenario.load_configs[0] = scenario.load_configs[0].model_copy(
        update={"stations": {"S1": ["BOMBx2"], "S2": ["BOMBx1"]}}
    )
    assert run(ctx, horizon_hours=4).totals == {"B1": 2}


def test_no_load_configs_permits_unarmed_support_capacity(ctx):
    ctx.scenario.snapshot().load_configs.clear()
    ctx.scenario.snapshot().stocks.clear()
    assert run(ctx, horizon_hours=2).totals == {"B1": 2}


def test_approved_assumptions_and_surge(ctx):
    scenario = ctx.scenario.snapshot()
    scenario.aircraft.extend(aircraft(tail_no=f"A{i}") for i in range(2, 11))
    scenario.crew.extend(crew(crew_id=f"C{i}", callsign=f"C{i}") for i in range(2, 11))
    scenario.bases[0] = scenario.bases[0].model_copy(update={"ground_crew_teams": 3})
    normal = build_problem(ComputeSortieCapacityInput(start=T, horizon_hours=4), ctx)
    surge = build_problem(
        ComputeSortieCapacityInput(start=T, horizon_hours=4, surge=True), ctx
    )
    assert normal.bases[0].fuel_kg == 80000
    assert normal.bases[0].groups[0].fuel_kg == 1000
    assert surge.bases[0].ground_hours == pytest.approx(3.45)
    assert surge.bases[0].groups[0].turnaround_hours == pytest.approx(0.85)
    assert run(ctx, horizon_hours=4).capacity == {"B1": [3] * 4}
    assert run(ctx, horizon_hours=4, surge=True).capacity == {"B1": [4] * 4}


def test_nonoptimal_solver_never_fabricates_capacity(ctx, monkeypatch):
    import pulp

    monkeypatch.setattr(pulp.LpProblem, "solve", lambda *a, **kw: -1)
    with pytest.raises(ToolInputError, match="No optimal"):
        run(ctx, horizon_hours=2)
