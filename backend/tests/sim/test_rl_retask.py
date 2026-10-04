import asyncio
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from sim_fixtures import T0, assignment, ctx, leg, mission, plan, scenario, threat

from garuda.contracts.events import Event
from garuda.contracts.tool import ToolInputError
from garuda.sim.rl_retask.baseline import GREEDY_VERSION, greedy_policy
from garuda.sim.rl_retask.decisions import decision_value
from garuda.sim.rl_retask.model import load_policy, run_policy, solve_milp, train_policy
from garuda.sim.rl_retask.problem import DecisionInstance, build_problem
from garuda.sim.rl_retask.schemas import RlRetaskInput
from garuda.sim.rl_retask.tool import rl_retask


def event(kind: str, entity: str | None = None, **params) -> Event:
    return Event(type=kind, entity_id=entity, time=T0, params=params)


def run(context, ev: Event, plan_id: str = "P1"):
    return asyncio.run(rl_retask(RlRetaskInput(plan_id=plan_id, event=ev), context))


def instance(values, feasible, p_success, priorities=None) -> DecisionInstance:
    feasible = np.array(feasible, dtype=bool)
    return DecisionInstance(
        values=np.array(values, dtype=float),
        priorities=np.array(priorities or [1] * len(values), dtype=float),
        feasible=feasible,
        p_success=np.array(p_success, dtype=float),
        distance_km=np.zeros(feasible.shape),
    )


def test_nmc_aircraft_is_replaced_and_the_new_plan_is_stored():
    context = ctx()
    out = run(context, event("aircraft_nmc", "SU-1"))
    assert out.policy_version == GREEDY_VERSION
    assert out.decision_latency_ms >= 0
    [(old, new)] = out.diff.changed
    assert old.tail_no == "SU-1" and new.mission_id == "M1"
    assert new.tail_no in {"SU-2", "SU-3", "RF-1"} and new.tail_no != "SU-2"
    assert new.tot == old.tot and new.depart < new.tot
    stored = context.plans.get(out.new_plan_id)
    assert stored.parent_plan_id == "P1"
    assert {(a.tail_no, a.mission_id) for a in stored.assignments} == {(new.tail_no, "M1"), ("SU-2", "M2")}
    assert out.compare_with_milp["value_gap"] == pytest.approx(0.0, abs=1e-9)
    assert out.compare_with_milp["rl_filled"] == 1.0


def test_rafale_gets_its_own_load_config():
    world = scenario(aircraft=[a for a in scenario().aircraft if a.tail_no in {"SU-1", "SU-2", "RF-1"}])
    out = run(ctx(world), event("aircraft_nmc", "SU-1"))
    [(_, new)] = out.diff.changed
    assert (new.tail_no, new.config_id) == ("RF-1", "CFG-RF-STRIKE")


def test_airborne_sorties_are_never_touched():
    airborne = plan([assignment("SU-1", "M1", depart_h=-0.5, tot_h=0.5), assignment("SU-2", "M2")])
    out = run(ctx(scenario(), airborne), event("aircraft_nmc", "SU-1"))
    assert out.diff.changed == [] and out.diff.removed == []


def test_closed_base_moves_sorties_to_the_other_base():
    context = ctx()
    out = run(context, event("base_closed", "B1"))
    replacements = [new for _, new in out.diff.changed]
    removed = out.diff.removed
    assert len(replacements) + len(removed) == 2
    assert replacements and all(new.tail_no in {"SU-3", "RF-1"} for new in replacements)


def test_weather_window_drops_the_sorties_inside_it():
    context = ctx()
    out = run(context, event("weather_override", start=T0.isoformat(), end=(T0 + timedelta(hours=4)).isoformat()))
    assert {a.mission_id for a in out.diff.removed} == {"M1", "M2"}
    stored = context.plans.get(out.new_plan_id)
    assert {u.reason for u in stored.unassigned} == {"WEATHER"}
    assert stored.assignments == []


def test_p1_request_adds_a_new_sortie():
    world = scenario(missions=[*scenario().missions, mission("M3", "T2", 8.0, priority=1)])
    out = run(ctx(world), event("p1_request", "M3"))
    [added] = out.diff.added
    assert added.mission_id == "M3" and added.tail_no not in {"SU-1", "SU-2"}


def test_new_sam_displaces_the_exposed_sortie():
    sam = {"lat": 28.8, "lon": 77.8, "range_km": 40.0, "pk": 0.9, "confidence": 0.95}
    legs = [leg("SU-1", "M1", (28.0, 77.0), (29.0, 78.0))]
    out = run(ctx(scenario(), plan(legs=legs)), event("new_sam", "SAM-NEW", **sam))
    touched = [old for old, _ in out.diff.changed] + out.diff.removed
    assert [a.tail_no for a in touched] == ["SU-1"]
    for _, new in out.diff.changed:
        assert new.tail_no in {"SU-3", "RF-1"}


def test_new_sam_without_a_position_is_an_actionable_error():
    with pytest.raises(ToolInputError, match="lat"):
        run(ctx(), event("new_sam", "SAM-NEW"))


def test_lost_tanker_leaves_a_long_aar_sortie_unserved():
    far = scenario(aircraft=[a for a in scenario().aircraft if a.tail_no in {"SU-1", "SU-2", "IL-1"}])
    for jet in far.aircraft:
        jet.range_km = 200.0
    legs = [leg("SU-1", "M1", (28.0, 77.0), (29.0, 78.0), needs_aar=True)]
    context = ctx(far, plan(legs=legs))
    out = run(context, event("tanker_lost", "IL-1"))
    assert [a.tail_no for a in out.diff.removed] == ["SU-1"]
    assert {u.reason for u in context.plans.get(out.new_plan_id).unassigned} == {"NO_TANKER"}


def test_threat_cap_masks_every_candidate_behind_a_dense_sam_belt():
    belt = [threat(f"S{i}", 29.0, 78.0, range_km=400.0, pk=0.9, confidence=0.95) for i in range(2)]
    built = build_problem(RlRetaskInput(plan_id="P1", event=event("aircraft_nmc", "SU-1")), ctx(scenario(threats=belt)))
    assert not built.instance.feasible.any()
    assert built.reasons == ["THREAT_RISK"]


@pytest.mark.parametrize(
    "ev, message",
    [
        (event("aircraft_nmc", "NOPE"), "known tail"),
        (event("base_closed", "B9"), "known base"),
        (event("p1_request", "M9"), "mission id"),
    ],
)
def test_unknown_entities_are_actionable_errors(ev, message):
    with pytest.raises(ToolInputError, match=message):
        run(ctx(), ev)


def test_unknown_plan_is_an_actionable_error():
    with pytest.raises(ToolInputError, match="unknown plan_id"):
        run(ctx(), event("aircraft_nmc", "SU-1"), plan_id="P9")


def test_milp_beats_greedy_under_contention():
    contested = instance([10.0, 9.0], [[1, 1], [1, 0]], [[0.9, 0.85], [0.9, 0.0]])
    greedy = run_policy(contested, greedy_policy())
    exact = solve_milp(contested)
    assert greedy == [0, None]
    assert exact == [1, 0]
    assert decision_value(contested, exact) > decision_value(contested, greedy)


def test_policy_never_takes_a_masked_or_reused_candidate():
    picks_zero = lambda obs, mask: 0  # noqa: E731
    shared = instance([5.0, 4.0, 3.0], [[1, 1], [1, 1], [0, 1]], [[0.9, 0.9]] * 3)
    assert run_policy(shared, picks_zero) == [0, None, None]


def test_stochastic_greedy_only_picks_open_candidates():
    open_ = instance([5.0], [[0, 1, 1]], [[0.9, 0.5, 0.9]])
    picks = {run_policy(open_, greedy_policy(False, seed))[0] for seed in range(20)}
    assert picks <= {1, 2} and len(picks) == 2


def test_stored_policy_is_used_when_the_model_store_has_one():
    class Stored:
        policy_version = "maskable-ppo-test"

        def predict(self, obs, action_masks=None, deterministic=True):
            return int(np.flatnonzero(action_masks)[0]), None

    act, version = load_policy(SimpleNamespace(models=SimpleNamespace(load=lambda name: Stored()), seed=1), True)
    assert version == "maskable-ppo-test"
    assert run_policy(instance([1.0], [[0, 1]], [[0.0, 0.8]]), act) == [1]


def test_env_passes_the_gymnasium_checker():
    from gymnasium.utils.env_checker import check_env

    from garuda.sim.rl_retask.env import GarudaRetaskEnv, random_instance

    check_env(GarudaRetaskEnv(random_instance), skip_render_check=True)


def test_maskable_ppo_trains_and_respects_masks():
    model = train_policy(total_timesteps=512, seed=3)
    act, version = load_policy(SimpleNamespace(models=SimpleNamespace(load=lambda name: model), seed=3), True)
    assert version == "maskable-ppo-512"
    decision = run_policy(instance([3.0, 2.0], [[0, 1], [1, 0]], [[0.0, 0.9], [0.8, 0.0]]), act)
    assert all(a is None or a == ok for a, ok in zip(decision, [1, 0]))
