import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import Assignment, FlightLeg, PlanDraft, PlanKPIs, UnassignedMission
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.providers.protocols import Providers
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.gis import BaseRecord, TerrainMeta
from garuda.schemas.mission import GeneratedScenario, Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import LoadConfig, TARGET_CLASSES, WeaponType
from garuda.sim.evaluate_plan_monte_carlo.explain import explain
from garuda.sim.evaluate_plan_monte_carlo.model import Solution, solve
from garuda.sim.evaluate_plan_monte_carlo.problem import build_problem, exposure_hours
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloInput, EvaluatePlanMonteCarloOutput
from garuda.sim.evaluate_plan_monte_carlo.tool import SPEC, evaluate_plan_monte_carlo

NOW = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)


def mission(mid="M1", **changes):
    values = dict(mission_id=mid, type="STRIKE", priority=1, value=100.0,
                  requested_by="test", window_start=NOW, window_end=NOW + timedelta(hours=6),
                  duration_min=30, target_id=None, area_wkt=None,
                  required_capabilities=["AG"], min_aircraft=1, max_aircraft=4,
                  required_effect="DESTROY", min_pk=0.8)
    return Mission(**(values | changes))


def aircraft(tail="A1", **changes):
    values = dict(tail_no=tail, type="Rafale", sqn_id="SQ1", base_id="B1", status="FMC",
                  fuel_kg=1000, fuel_capacity_kg=2000, flight_hours_total=100,
                  hours_to_next_inspection=100, inspection_cycle_hours=200,
                  turnaround_min=10, capabilities=["AG"], range_km=1000,
                  cruise_kts=400, ceiling_ft=40000, updated_at=NOW)
    return Aircraft(**(values | changes))


def assignment(mid="M1", tail="A1", minute=30):
    return Assignment(tail_no=tail, config_id="CFG", mission_id=mid,
                      depart=NOW + timedelta(minutes=minute - 30),
                      tot=NOW + timedelta(minutes=minute))


def leg(mid="M1", tail="A1", minute=30, needs_aar=False, altitude=10000):
    # Exactly one hour stationary inside the threat envelope, when present.
    return FlightLeg(mission_id=mid, tail_no=tail, needs_aar=needs_aar,
                     waypoints=[Waypoint(lat=0, lon=0, alt_ft=altitude,
                                         eta=NOW + timedelta(minutes=minute - 30)),
                                Waypoint(lat=0, lon=0, alt_ft=altitude,
                                         eta=NOW + timedelta(minutes=minute + 30))])


def threat(**changes):
    return Threat(**(dict(threat_id="T1", type="SAM_MED", lon=0, lat=0, mobile=False,
                         engagement_range_km=10, detection_range_km=20, min_alt_ft=0,
                         max_alt_ft=20000, pk_base=0.2, confidence=1,
                         first_seen=NOW, last_seen=NOW, status="ACTIVE") | changes))


def context(missions=None, aircraft_list=None, assignments=None, legs=None,
            threats=None, unassigned=None, availability=1.0, failure=0.0,
            weather=1.0, pk=1.0, threshold=100.0):
    scenario = GeneratedScenario(
        seed=42, missions=missions if missions is not None else [mission()],
        aircraft=aircraft_list if aircraft_list is not None else [aircraft()],
        threats=threats or [], targets=[], tracks=[], stocks=[], compatibilities=[],
        weapons=[WeaponType(weapon_code="W", category="LGB", range_km=10, weight_kg=100,
                            pk_by_target_class={target: 0.5 for target in TARGET_CLASSES},
                            min_release_alt_ft=0, guidance="GPS")],
        load_configs=[LoadConfig(config_id="CFG", aircraft_type="Rafale", role="STRIKE",
                                 stations={"S1": ["Wx2"]}, total_weight_kg=200, range_penalty_pct=0)],
        cells=[], observations=[], bases=[BaseRecord(base_id="B1", name="test", icao="TEST",
                                                      lat=0, lon=0, elevation_ft=0, runway_count=1,
                                                      runway_length_m=1000, shelter_count=0,
                                                      fuel_capacity_l=1000, ground_crew_teams=1,
                                                      max_sorties_per_hour=10)],
        features=[], borders=[], distances=[], terrain=TerrainMeta(lat_min=0, lat_max=1,
                                                                   lon_min=0, lon_max=1, cell_km=1,
                                                                   rows=1, cols=1, elev_min_m=0, elev_max_m=0),
        aircraft_types=[], squadrons=[], aircraft_history=[], crew=[], crew_duty=[], zones=[], adsb_tracks=[])
    plan = PlanDraft(plan_id="plan", scenario_time=NOW,
                     assignments=assignments if assignments is not None else [assignment()],
                     legs=legs if legs is not None else [leg()], unassigned=unassigned or [],
                     kpis=PlanKPIs(value=threshold))

    def read_plan(plan_id):
        if plan_id != plan.plan_id:
            raise KeyError(plan_id)
        return plan

    def probability(source, key):
        return source.get(key, 1.0) if isinstance(source, dict) else source

    providers = Providers(
        availability=SimpleNamespace(p_fmc=lambda tail, at: probability(availability, tail)),
        component_risk=SimpleNamespace(aircraft_risk=lambda tail, hours: probability(failure, tail)),
        weather_go=SimpleNamespace(p_go=lambda base, route, window, guidance: weather),
        pk=SimpleNamespace(pk=lambda config, mid: probability(pk, mid)),
        route=None, capacity=None, fatigue=None, threat_risk=None)
    ctx = ToolContext(scenario=SimpleNamespace(snapshot=lambda: scenario),
                      plans=SimpleNamespace(get=read_plan), models=None, providers=providers,
                      scenario_time=NOW, seed=999)
    return ctx, plan, scenario


def evaluate(ctx, **changes):
    return asyncio.run(evaluate_plan_monte_carlo(EvaluatePlanMonteCarloInput(plan_id="plan", **changes), ctx))


def test_public_contract_and_defaults_remain_unchanged():
    payload = EvaluatePlanMonteCarloInput(plan_id="plan")
    assert payload.model_dump() == dict(plan_id="plan", n_runs=5000, seed=42, stress="none")
    assert set(EvaluatePlanMonteCarloOutput.model_fields) == {
        "value", "mission_success", "expected_losses", "p_value_above_threshold", "cvar_5", "sensitivities", "weakest_links"}
    assert SPEC.input_model is EvaluatePlanMonteCarloInput
    assert SPEC.output_model is EvaluatePlanMonteCarloOutput
    assert SPEC.side_effects == "none" and SPEC.timeout_s == 30


def test_certain_outcome_and_no_side_effects():
    ctx, plan, scenario = context()
    before = plan.model_dump(), scenario.model_dump()
    result = evaluate(ctx)
    assert result.value.model_dump() == dict(mean=100, p5=100, p50=100, p95=100)
    assert result.mission_success == {"M1": 1.0}
    assert result.expected_losses == 0
    assert result.p_value_above_threshold == 1 and result.cvar_5 == 100
    assert result.weakest_links == []
    assert all(s.effect == 0 for s in result.sensitivities)
    assert (plan.model_dump(), scenario.model_dump()) == before


def test_single_mission_matches_analytic_probability_with_one_sam():
    ctx, _, _ = context(availability=0.8, weather=0.7, pk=0.6, failure=0.1, threats=[threat()])
    result = evaluate(ctx, n_runs=50000)
    expected = 0.8 * 0.7 * 0.6 * 0.9 * 0.8
    assert result.mission_success["M1"] == pytest.approx(expected, abs=0.008)
    assert result.value.mean == pytest.approx(100 * expected, abs=0.8)
    assert result.expected_losses == pytest.approx(0.8 * 0.7 * (1 - 0.9 * 0.8), abs=0.008)


def test_payload_seed_is_reproducible_and_independent_of_context_and_global_rng():
    ctx, _, _ = context(availability=0.6, weather=0.7, pk=0.8, threats=[threat()])
    np.random.seed(123)
    global_before = np.random.get_state()
    first = evaluate(ctx, seed=7)
    second = evaluate(replace(ctx, seed=1234), seed=7)
    assert first == second
    assert first != evaluate(ctx, seed=8)
    global_after = np.random.get_state()
    assert global_before[0] == global_after[0]
    np.testing.assert_array_equal(global_before[1], global_after[1])
    assert global_before[2:] == global_after[2:]


@pytest.mark.parametrize("n_runs", [1, 7, 20])
def test_small_run_counts(n_runs):
    ctx, _, _ = context()
    result = evaluate(ctx, n_runs=n_runs)
    assert result.cvar_5 == pytest.approx(100)
    assert result.value.mean == 100


@pytest.mark.parametrize("changes, message", [({"n_runs": 0}, "n_runs"),
                                              ({"n_runs": -1}, "n_runs"),
                                              ({"seed": -1}, "seed")])
def test_invalid_simulation_options(changes, message):
    ctx, _, _ = context()
    with pytest.raises(ToolInputError, match=message):
        evaluate(ctx, **changes)


def test_missing_plan_and_invalid_provider_probability():
    ctx, _, _ = context()
    with pytest.raises(ToolInputError, match="Unknown plan_id"):
        asyncio.run(evaluate_plan_monte_carlo(EvaluatePlanMonteCarloInput(plan_id="missing"), ctx))
    ctx, _, _ = context(weather=float("nan"))
    with pytest.raises(ToolInputError, match="weather_go"):
        evaluate(ctx)


def test_empty_and_unassigned_plans():
    ctx, _, _ = context(assignments=[], legs=[], threshold=1)
    empty = evaluate(ctx)
    assert empty.mission_success == {} and empty.value.mean == 0
    assert empty.expected_losses == 0 and empty.cvar_5 == 0
    assert empty.p_value_above_threshold == 0
    ctx, _, _ = context(assignments=[], legs=[], unassigned=[UnassignedMission(mission_id="M1", reason="NO_AIRCRAFT")])
    result = evaluate(ctx)
    assert result.mission_success == {"M1": 0} and result.weakest_links == ["M1"]


def test_threshold_uses_plan_kpis_value_inclusively():
    ctx, plan, _ = context(pk=0.5, threshold=100)
    result = evaluate(ctx)
    assert result.p_value_above_threshold == result.mission_success["M1"]
    plan.kpis.value = 101
    assert evaluate(ctx).p_value_above_threshold == 0
    plan.kpis.value = 0
    assert evaluate(ctx).p_value_above_threshold == 1


def test_cvar_uses_exact_lower_tail_mass_with_ties():
    ctx, _, _ = context()
    problem = build_problem(EvaluatePlanMonteCarloInput(plan_id="plan", n_runs=100), ctx)
    values = np.array([0.0, 0.0] + [100.0] * 98)
    solution = Solution(values, (values > 0)[:, None], np.zeros(100), {})
    assert explain(problem, solution).cvar_5 == 60
    problem = replace(problem, n_runs=21)
    values = np.array([0.0] + [100.0] * 20)
    solution = Solution(values, (values > 0)[:, None], np.zeros(21), {})
    assert explain(problem, solution).cvar_5 == pytest.approx(100 * 0.05 / 1.05)


def test_spare_rescues_ground_abort_and_is_not_double_booked():
    ctx, _, _ = context(aircraft_list=[aircraft(), aircraft("SPARE")], availability={"A1": 0})
    assert evaluate(ctx).mission_success["M1"] == 1
    ctx, _, _ = context(missions=[mission("M1"), mission("M2")],
                        aircraft_list=[aircraft(), aircraft("A2"), aircraft("SPARE")],
                        assignments=[assignment(), assignment("M2", "A2")],
                        legs=[leg(), leg("M2", "A2")], availability={"A1": 0, "A2": 0})
    result = evaluate(ctx)
    assert sum(result.mission_success.values()) == 1
    assert result.value.mean == 100


def test_simultaneous_sorties_share_spare_availability_draw():
    ctx, _, _ = context(missions=[mission("M1"), mission("M2")],
                        aircraft_list=[aircraft(), aircraft("A2"), aircraft("SPARE")],
                        assignments=[assignment(), assignment("M2", "A2")],
                        legs=[leg(), leg("M2", "A2")],
                        availability={"A1": 0, "A2": 0, "SPARE": 0.5})
    result = evaluate(ctx, n_runs=30000)
    assert sum(result.mission_success.values()) == pytest.approx(0.5, abs=0.01)


def test_observation_missions_do_not_require_weapon_pk_or_load_configuration():
    ctx, plan, _ = context(missions=[mission(type="AAR", required_effect="OBSERVE")])
    plan.assignments[0].config_id = "NO-WEAPONS"

    def unavailable_pk(*args):
        raise AssertionError("Weapon provider must not be called for an AAR sortie")

    ctx.providers.pk.pk = unavailable_pk
    assert evaluate(ctx).mission_success["M1"] == 1


def test_lost_aircraft_cannot_fly_again_and_losses_are_unique():
    ctx, _, _ = context(missions=[mission("M1"), mission("M2")],
                        assignments=[assignment(), assignment("M2", minute=180)],
                        legs=[leg(), leg("M2", minute=180)], failure=1)
    result = evaluate(ctx)
    assert result.mission_success == {"M1": 0, "M2": 0}
    assert result.expected_losses == 1


def test_overlapping_sorties_do_not_reuse_airborne_primary():
    ctx, _, _ = context(missions=[mission("M1"), mission("M2")],
                        assignments=[assignment(), assignment("M2", minute=40)],
                        legs=[leg(), leg("M2", minute=40)])
    result = evaluate(ctx)
    assert result.mission_success == {"M1": 1, "M2": 0}


def test_weather_is_correlated_within_day():
    ctx, _, _ = context(missions=[mission("M1"), mission("M2")],
                        aircraft_list=[aircraft(), aircraft("A2")],
                        assignments=[assignment(), assignment("M2", "A2")],
                        legs=[leg(), leg("M2", "A2")], weather=0.5)
    problem = build_problem(EvaluatePlanMonteCarloInput(plan_id="plan"), ctx)
    outcomes = solve(problem)
    np.testing.assert_array_equal(outcomes.mission_success[:, 0], outcomes.mission_success[:, 1])


def test_minimum_aircraft_and_configuration_effect_are_aggregated_once():
    ctx, _, _ = context(missions=[mission(min_aircraft=2)],
                        aircraft_list=[aircraft(), aircraft("A2")],
                        assignments=[assignment(), assignment(tail="A2")],
                        legs=[leg(), leg(tail="A2")], pk=0.5)
    assert evaluate(ctx, n_runs=30000).mission_success["M1"] == pytest.approx(0.75, abs=0.01)
    ctx, _, _ = context(missions=[mission(min_aircraft=2)])
    assert evaluate(ctx).mission_success["M1"] == 0


def test_stress_modes_degrade_weather_and_threats():
    ctx, _, _ = context(weather=0.8, threats=[threat(pk_base=0.4)])
    normal = evaluate(ctx, n_runs=20000)
    bad_weather = evaluate(ctx, n_runs=20000, stress="degraded_weather")
    heavy = evaluate(ctx, n_runs=20000, stress="heavy_threat")
    assert bad_weather.value.mean < normal.value.mean
    assert heavy.value.mean < normal.value.mean
    assert heavy.expected_losses > normal.expected_losses


def test_tanker_failure_drops_only_aar_dependent_missions():
    ctx, _, _ = context(missions=[mission("TANKER", type="AAR", required_effect="OBSERVE", duration_min=90),
                                  mission("RECEIVER", dependencies=["TANKER"]), mission("INDEPENDENT")],
                        aircraft_list=[aircraft("T"), aircraft("R"), aircraft("I")],
                        assignments=[assignment("TANKER", "T"), assignment("RECEIVER", "R", 60),
                                     assignment("INDEPENDENT", "I", 60)],
                        legs=[leg("TANKER", "T"), leg("RECEIVER", "R", 60, needs_aar=True),
                              leg("INDEPENDENT", "I", 60)])
    assert evaluate(ctx).mission_success == {"INDEPENDENT": 1, "RECEIVER": 1, "TANKER": 1}
    result = evaluate(ctx, stress="tanker_loss")
    assert result.mission_success == {"INDEPENDENT": 1, "RECEIVER": 0, "TANKER": 0}
    assert result.expected_losses == 1


def test_aar_flag_without_tanker_and_expired_tanker():
    ctx, _, _ = context(legs=[leg(needs_aar=True)])
    assert evaluate(ctx).mission_success["M1"] == 0
    ctx, _, _ = context(missions=[mission("TANKER", type="AAR"), mission("M1")],
                        aircraft_list=[aircraft(), aircraft("T")],
                        assignments=[assignment("TANKER", "T"), assignment(minute=180)],
                        legs=[leg("TANKER", "T"), leg(minute=180, needs_aar=True)])
    assert evaluate(ctx).mission_success["M1"] == 0


def test_failed_or_missing_sead_increases_risk_without_cancelling_strike():
    ctx, plan, _ = context(missions=[mission("SEAD", type="SEAD"), mission("STRIKE", dependencies=["SEAD"])],
                           aircraft_list=[aircraft("S"), aircraft("A1")],
                           assignments=[assignment("SEAD", "S"), assignment("STRIKE", minute=90)],
                           legs=[leg("SEAD", "S", altitude=30000), leg("STRIKE", minute=90)],
                           threats=[threat(pk_base=0.8)], pk={"SEAD": 1, "STRIKE": 1})
    supported = evaluate(ctx, n_runs=20000)
    ctx.providers.pk.pk = lambda config, mid: 0 if mid == "SEAD" else 1
    failed = evaluate(ctx, n_runs=20000)
    assert supported.mission_success["STRIKE"] > failed.mission_success["STRIKE"] + 0.4
    assert failed.mission_success["STRIKE"] > 0
    plan.assignments = [a for a in plan.assignments if a.mission_id != "SEAD"]
    missing = evaluate(ctx)
    assert missing.mission_success["SEAD"] == 0 and missing.mission_success["STRIKE"] > 0


def test_rejects_cyclic_dependencies_and_bad_references():
    ctx, _, _ = context(missions=[mission(dependencies=["M1"])])
    with pytest.raises(ToolInputError, match="Cyclic"):
        evaluate(ctx)
    ctx, plan, _ = context()
    plan.assignments[0].tail_no = "missing"
    with pytest.raises(ToolInputError, match="Unknown aircraft"):
        evaluate(ctx)
    ctx, plan, _ = context()
    plan.assignments.append(plan.assignments[0])
    with pytest.raises(ToolInputError, match="Duplicate"):
        evaluate(ctx)


def test_exposure_clips_crossing_segments_and_altitude():
    t = threat(engagement_range_km=111.195, detection_range_km=200)
    path = [Waypoint(lat=0, lon=-2, alt_ft=10000, eta=NOW),
            Waypoint(lat=0, lon=2, alt_ft=10000, eta=NOW + timedelta(hours=1))]
    assert exposure_hours(path, t) == pytest.approx(0.5)
    path[1].alt_ft = path[0].alt_ft = 30000
    assert exposure_hours(path, t) == 0
    path[0].alt_ft, path[1].alt_ft = 0, 40000
    assert exposure_hours(path, t) == pytest.approx(0.25)
    t.status = "DESTROYED"
    assert exposure_hours(path, t) == 0


def test_separate_leg_exposures_compound_survival():
    first, second = leg(), leg()
    first.waypoints[1].eta = NOW + timedelta(minutes=15)
    second.waypoints[0].eta = NOW + timedelta(minutes=30)
    second.waypoints[1].eta = NOW + timedelta(minutes=45)
    ctx, _, _ = context(legs=[first, second], threats=[threat(pk_base=1)])
    result = evaluate(ctx, n_runs=30000)
    assert result.mission_success["M1"] == pytest.approx(0.75 ** 2, abs=0.01)


def test_fallback_route_and_waypoint_validation():
    ctx, _, _ = context(legs=[])
    assert evaluate(ctx).mission_success["M1"] == 1
    ctx, plan, _ = context()
    plan.legs[0].waypoints[1].eta = NOW - timedelta(hours=1)
    with pytest.raises(ToolInputError, match="ETAs"):
        evaluate(ctx)


def test_sensitivities_have_expected_direction_and_finite_effects():
    ctx, _, _ = context(pk=0.5)
    result = evaluate(ctx)
    effects = {s.factor: s.effect for s in result.sensitivities}
    assert effects["weapon_effect"] == pytest.approx(1)
    assert all(np.isfinite(effect) and -1 <= effect <= 1 for effect in effects.values())
