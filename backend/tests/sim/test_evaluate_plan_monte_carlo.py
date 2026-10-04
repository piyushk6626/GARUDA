import asyncio

import numpy as np
import pytest
from sim_fixtures import assignment, ctx, leg, mission, plan, scenario, threat

from garuda.contracts.tool import ToolInputError
from garuda.sim import kernel
from garuda.sim.evaluate_plan_monte_carlo.baseline import baseline
from garuda.sim.evaluate_plan_monte_carlo.model import solve
from garuda.sim.evaluate_plan_monte_carlo.problem import build_problem
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloInput
from garuda.sim.evaluate_plan_monte_carlo.tool import evaluate_plan_monte_carlo

MID_PATH_SAM = threat("SAM-1", 28.5, 77.5, range_km=40.0, pk=0.5, confidence=0.8)


def run(context, **kw):
    payload = EvaluatePlanMonteCarloInput(plan_id="P1", **kw)
    return asyncio.run(evaluate_plan_monte_carlo(payload, context))


def problem(context, **kw):
    return build_problem(EvaluatePlanMonteCarloInput(plan_id="P1", **kw), context)


def test_no_threat_success_matches_the_analytic_launch_times_kill():
    context = ctx()
    built = problem(context, n_runs=20_000)
    simulated = solve(built).outcomes.mission_success.mean(axis=0)
    analytic = baseline(built)
    assert simulated[0] == pytest.approx(analytic["M1"], abs=0.01)
    assert simulated[1] == pytest.approx(analytic["M2"], abs=0.01)
    assert built.sorties[0].exposure == ()
    assert 0.0 < analytic["M1"] < 0.97


def test_one_sam_on_the_path_matches_the_analytic_check_and_costs_aircraft():
    context = ctx(scenario(threats=[MID_PATH_SAM]))
    built = problem(context, n_runs=20_000)
    assert built.sorties[0].exposure[0] > 0.5
    simulation = solve(built)
    assert simulation.outcomes.mission_success.mean(axis=0)[0] == pytest.approx(baseline(built)["M1"], abs=0.015)
    assert simulation.outcomes.losses.mean() > 0.1


def test_output_fields_are_consistent():
    out = run(ctx(scenario(threats=[MID_PATH_SAM])), n_runs=4000)
    assert out.value.p5 <= out.value.p50 <= out.value.p95
    assert out.cvar_5 <= out.value.p5 + 1e-9
    assert set(out.mission_success) == {"M1", "M2"}
    assert 0.0 <= out.p_value_above_threshold <= 1.0
    assert out.weakest_links[0] == "M1"
    assert {s.factor for s in out.sensitivities} >= {"aircraft_availability", "threat_strength"}
    effects = [abs(s.effect) for s in out.sensitivities]
    assert effects == sorted(effects, reverse=True)


def test_same_seed_reproduces_and_another_seed_differs():
    context = ctx(scenario(threats=[MID_PATH_SAM]))
    first, again, other = run(context, seed=7), run(context, seed=7), run(context, seed=8)
    assert first == again
    assert first.value.mean != other.value.mean


def test_degraded_weather_lowers_value():
    context = ctx()
    assert run(context, stress="degraded_weather").value.mean < run(context).value.mean - 1.0


def test_heavy_threat_raises_losses():
    context = ctx(scenario(threats=[MID_PATH_SAM]))
    assert run(context, stress="heavy_threat").expected_losses > run(context).expected_losses


def test_tanker_loss_fails_the_aar_dependent_mission():
    legs = [leg("SU-1", "M1", (28.0, 77.0), (29.0, 78.0), needs_aar=True)]
    context = ctx(scenario(), plan(legs=legs))
    assert run(context).mission_success["M1"] > 0.5
    stressed = run(context, stress="tanker_loss")
    assert stressed.mission_success["M1"] == 0.0
    assert stressed.mission_success["M2"] > 0.5


def test_a_failed_dependency_fails_the_dependent_mission():
    world = scenario(missions=[mission("M1", "T1", 10.0), mission("M2", "T2", 6.0, dependencies=["M1"])])
    built = problem(ctx(world), n_runs=3000)
    success = solve(built).outcomes.mission_success
    assert not np.any(success[:, 1] & ~success[:, 0])


def test_sead_success_protects_the_later_strike():
    sam_at_target = threat("SAM-T", 29.0, 78.0, range_km=60.0, pk=0.6, confidence=0.9)
    world = scenario(
        threats=[sam_at_target],
        missions=[mission("M0", "T1", 2.0, kind="SEAD"), mission("M1", "T1", 10.0)],
    )
    strike_only = plan([assignment("SU-2", "M1")])
    with_sead = plan([assignment("SU-1", "M0", 1.0, 2.0, "CFG-SU-SEAD"), assignment("SU-2", "M1")])
    alone = run(ctx(world, strike_only), n_runs=8000).mission_success["M1"]
    escorted = run(ctx(world, with_sead), n_runs=8000).mission_success["M1"]
    assert escorted > alone + 0.05


def test_explicit_value_threshold_is_used():
    out = run(ctx(), value_threshold=0.0)
    assert out.p_value_above_threshold == 1.0


@pytest.mark.parametrize(
    "context, message",
    [
        (ctx(scenario(), plan(plan_id="OTHER")), "unknown plan_id"),
        (ctx(scenario(), plan([])), "no assignments"),
        (ctx(scenario(), plan([assignment("XX-9", "M1")])), "unknown tail numbers"),
        (ctx(scenario(), plan([assignment("SU-1", "M9")])), "unknown mission ids"),
    ],
)
def test_bad_plans_are_actionable_errors(context, message):
    with pytest.raises(ToolInputError, match=message):
        run(context)


def test_run_count_is_bounded():
    with pytest.raises(ValueError):
        EvaluatePlanMonteCarloInput(plan_id="P1", n_runs=10)


def test_kernel_prior_uses_plan_risk():
    draft = plan()
    draft.kpis.risk = 0.5
    outcomes = kernel.simulate(draft, 4000, np.random.default_rng(0))
    assert outcomes.mission_success.shape == (4000, 2)
    assert outcomes.mission_success.mean() == pytest.approx(0.5, abs=0.03)
    assert outcomes.losses.mean() == pytest.approx(1.0, abs=0.05)


def test_kernel_weather_is_correlated_across_missions():
    draft = plan()
    go = kernel.sample_weather(draft, {"M1": 0.5, "M2": 0.5}, 20_000, np.random.default_rng(1))
    assert go.mean(axis=0) == pytest.approx([0.5, 0.5], abs=0.02)
    assert np.corrcoef(go[:, 0], go[:, 1])[0, 1] > 0.3
