import asyncio
from dataclasses import replace
from threading import Event as ThreadEvent
from types import SimpleNamespace

import numpy as np
import pytest

from garuda.contracts.events import Event
from garuda.contracts.plan import Assignment, CrewAssignment, FlightLeg, SortieSlot
from garuda.contracts.tool import ToolInputError
from garuda.sim.rl_retask.env import GarudaRetaskEnv
from garuda.sim.rl_retask.model import PolicyArtifact, selection_errors, solve
from garuda.sim.rl_retask.problem import build_problem
from garuda.sim.rl_retask.schemas import RlRetaskInput, RlRetaskOutput
from garuda.sim.rl_retask.tool import SPEC, rl_retask
from garuda.validation.plan_validator import Violation

# Reuse the established retasking scenario builder: both implementations must
# face identical aircraft, crew, weapon, capacity and event conventions.
from test_retask_rolling_horizon import NOW, aircraft, context, mission

import importlib

tool_module = importlib.import_module("garuda.sim.rl_retask.tool")


class FirstFeasible:
    def __init__(self):
        self.calls = []

    def predict(self, observation, *, deterministic, action_masks):
        self.calls.append((observation.copy(), deterministic, action_masks.copy()))
        return np.asarray(np.flatnonzero(action_masks)[1], dtype=np.int64), None


def request(kind="aircraft_nmc", entity="A1", params=None, deterministic=True):
    return RlRetaskInput(
        plan_id="original",
        event=Event(type=kind, entity_id=entity, params=params or {}, time=NOW),
        deterministic=deterministic,
    )


def with_policy(ctx, model=None, **metadata):
    artifact = PolicyArtifact(model or FirstFeasible(), "ppo-test-1", **metadata)
    return replace(ctx, models=SimpleNamespace(load=lambda name: artifact)), artifact


def run(ctx, payload=None):
    return asyncio.run(rl_retask(payload or request(), ctx))


def test_no_checkpoint_uses_real_validated_solver_and_keeps_parent():
    ctx, scenario = context()
    before = ctx.plans.get("original").model_dump()
    scenario_before = scenario.model_dump()
    output = run(ctx)
    assert output.policy_version == "milp-fallback"
    assert output.compare_with_milp["used_milp"] == 1
    assert output.compare_with_milp["constraint_violations"] == 0
    assert output.compare_with_milp["value_gap"] == 0
    child = ctx.plans.get(output.new_plan_id)
    assert child.parent_plan_id == "original" and child.status == "DRAFT"
    assert child.assignments[0].tail_no != "A1"
    assert output.diff.changed == [
        (ctx.plans.get("original").assignments[0], child.assignments[0])
    ]
    assert ctx.plans.get("original").model_dump() == before
    assert scenario.model_dump() == scenario_before
    assert len(ctx.plans.items) == 2
    assert output.decision_latency_ms > 0


@pytest.mark.parametrize("deterministic", [True, False])
def test_masked_policy_and_comparison(deterministic):
    ctx, _ = context()
    ctx, artifact = with_policy(ctx, max_aircraft=4, max_missions=3, max_bases=3)
    output = run(ctx, request(deterministic=deterministic))
    assert output.policy_version == artifact.version
    assert output.compare_with_milp["used_milp"] == 0
    assert artifact.model.calls
    for observation, flag, masks in artifact.model.calls:
        assert flag == deterministic
        assert observation.dtype == np.float32 and np.isfinite(observation).all()
        assert masks.dtype == bool and masks.shape == (13,)
        assert masks[0] and not masks[1]  # failed A1/M1
        assert not masks[2:4].any()  # padded missions
    child = ctx.plans.get(output.new_plan_id)
    assert child.assignments[0].tail_no == "A2"
    problem = build_problem(request(deterministic=deterministic), ctx)
    assert not selection_errors(problem, solve(problem, artifact))


@pytest.mark.parametrize("action", [-1, 1, 999999, 1.5, True, [2, 3]])
def test_invalid_action_falls_back_without_persisting_policy_plan(action):
    ctx, _ = context()
    ctx, _ = with_policy(
        ctx, SimpleNamespace(predict=lambda *args, **kwargs: (action, None))
    )
    output = run(ctx)
    assert output.policy_version == "ppo-test-1/milp-fallback"
    assert output.compare_with_milp["used_milp"] == 1
    assert ctx.plans.get(output.new_plan_id).assignments[0].tail_no != "A1"
    assert len(ctx.plans.items) == 2


def test_partial_multi_aircraft_package_is_rejected_and_repaired():
    ctx, _ = context(missions=[mission(min_aircraft=2, max_aircraft=2)])
    calls = []

    def predict(obs, *, action_masks, **kwargs):
        calls.append(1)
        return (int(np.flatnonzero(action_masks)[1]) if len(calls) == 1 else 0), None

    ctx, _ = with_policy(ctx, SimpleNamespace(predict=predict))
    output = run(ctx)
    assert output.compare_with_milp["used_milp"] == 1
    assert len(ctx.plans.get(output.new_plan_id).assignments) == 2


def test_complete_multi_aircraft_package_is_accepted():
    ctx, _ = context(missions=[mission(min_aircraft=2, max_aircraft=2)])
    ctx, _ = with_policy(ctx)
    output = run(ctx)
    assert output.compare_with_milp["used_milp"] == 0
    assert len(ctx.plans.get(output.new_plan_id).assignments) == 2


def test_dependency_mask_requires_completed_support_package():
    ctx, _ = context(missions=[mission("M1"), mission("M2", dependencies=["M1"])])
    ctx, artifact = with_policy(ctx, max_aircraft=4, max_missions=3)
    env = GarudaRetaskEnv(build_problem(request(), ctx), artifact)
    masks = env.action_masks()
    assert not any(masks[1 + i * 3 + 1] for i in range(4))
    env.step(1 + 1 * 3)  # A2 completes M1
    masks = env.action_masks()
    assert masks[1 + 2 * 3 + 1]  # A3 may now fly M2


def test_skip_is_a_valid_advisory_policy_with_honest_value_gap():
    ctx, _ = context()
    ctx, _ = with_policy(
        ctx, SimpleNamespace(predict=lambda *args, **kwargs: (0, None))
    )
    output = run(ctx)
    assert output.policy_version == "ppo-test-1"
    assert output.compare_with_milp["value_gap"] == -30
    assert output.compare_with_milp["used_milp"] == 0
    assert not ctx.plans.get(output.new_plan_id).assignments
    assert ctx.plans.get(output.new_plan_id).unassigned[0].mission_id == "M1"
    assert output.diff.removed


@pytest.mark.parametrize(
    "metadata",
    [
        {"max_aircraft": 1},
        {"max_missions": 0},
        {"max_bases": 1},
        {"capabilities": ()},
        {"capabilities": ("BVR", "BVR")},
        {"observation_version": "unknown"},
    ],
)
def test_incompatible_artifact_falls_back(metadata):
    ctx, _ = context()
    ctx, _ = with_policy(ctx, **metadata)
    assert run(ctx).compare_with_milp["used_milp"] == 1


@pytest.mark.parametrize(
    "field,space",
    [
        ("action_space", SimpleNamespace(n=2)),
        ("observation_space", SimpleNamespace(shape=(2,))),
    ],
)
def test_checkpoint_spaces_must_match_metadata(field, space):
    ctx, _ = context()
    model = FirstFeasible()
    setattr(model, field, space)
    ctx, _ = with_policy(ctx, model)
    assert run(ctx).compare_with_milp["used_milp"] == 1


@pytest.mark.parametrize("missing", [KeyError, FileNotFoundError, NotImplementedError])
def test_missing_checkpoint(missing):
    def load(name):
        assert name == "rl_retask"
        raise missing("missing")

    ctx, _ = context()
    assert (
        run(replace(ctx, models=SimpleNamespace(load=load))).policy_version
        == "milp-fallback"
    )


def test_unknown_plan_and_bad_event_do_not_write():
    ctx, _ = context()
    with pytest.raises(ToolInputError, match="Unknown plan_id"):
        run(ctx, request().model_copy(update={"plan_id": "missing"}))
    with pytest.raises(ToolInputError, match="known aircraft"):
        run(ctx, request(entity="missing"))
    assert len(ctx.plans.items) == 1


def test_frozen_conflict_never_returns_infeasible_parent_as_success():
    ctx, _ = context(missions=[mission(start=1)])
    with pytest.raises(ToolInputError, match="frozen assignments infeasible"):
        run(ctx)
    assert len(ctx.plans.items) == 1


def test_frozen_assignment_is_preserved_by_rl():
    ctx, _ = context(missions=[mission(start=1)])
    ctx, _ = with_policy(ctx)
    output = run(ctx, request(entity="A2"))
    assert (
        ctx.plans.get(output.new_plan_id).assignments
        == ctx.plans.get("original").assignments
    )
    assert not output.diff.changed and not output.diff.removed


@pytest.mark.parametrize(
    "kind,entity,params",
    [
        ("base_closed", "B1", {}),
        ("weather_override", "B1", {"p_go": 0}),
        ("p1_request", "M1", {}),
    ],
)
def test_event_overlays(kind, entity, params):
    ctx, _ = context()
    ctx, _ = with_policy(ctx)
    output = run(ctx, request(kind, entity, params))
    child = ctx.plans.get(output.new_plan_id)
    assert child.assignments
    if kind != "p1_request":
        assert child.assignments[0].tail_no == "A3"
    else:
        assert child.kpis.value == 100


def test_changed_assignment_drops_stale_downstream_artifacts():
    ctx, _ = context()
    parent = ctx.plans.get("original")
    parent.sortie_slots = [
        SortieSlot(
            slot_id="S",
            mission_id="M1",
            tail_no="A1",
            role="PILOT",
            aircraft_type="Rafale",
            start=parent.assignments[0].depart,
            end=parent.assignments[0].tot,
        )
    ]
    parent.crew_assignments = [
        CrewAssignment(crew_id="B1-P0", slot_id="S", role="PILOT")
    ]
    parent.legs = [FlightLeg(mission_id="M1", tail_no="A1", waypoints=[])]
    output = run(ctx)
    child = ctx.plans.get(output.new_plan_id)
    assert not child.sortie_slots and not child.crew_assignments and not child.legs
    assert parent.sortie_slots and parent.crew_assignments and parent.legs


def test_shared_checker_rejects_both_decisions_without_write(monkeypatch):
    ctx, _ = context()
    ctx, _ = with_policy(ctx)
    monkeypatch.setattr(
        tool_module,
        "validate_plan",
        lambda *args: [Violation(rule="airspace", entity_id="M1", detail="conflict")],
    )
    with pytest.raises(ToolInputError, match="constraint checker rejected"):
        run(ctx)
    assert len(ctx.plans.items) == 1


def test_shared_checker_can_reject_rl_and_accept_fallback(monkeypatch):
    ctx, _ = context()
    ctx, _ = with_policy(ctx)
    calls = []

    def validate(*args):
        calls.append(1)
        return (
            [Violation(rule="test", entity_id="M1", detail="policy rejected")]
            if len(calls) == 1
            else []
        )

    monkeypatch.setattr(tool_module, "validate_plan", validate)
    assert run(ctx).compare_with_milp["used_milp"] == 1
    assert len(calls) == 2 and len(ctx.plans.items) == 2


def test_unavailable_solver_preserves_valid_rl_decision(monkeypatch):
    ctx, _ = context()
    ctx, _ = with_policy(ctx)

    def unavailable(problem):
        raise ToolInputError("No solver")

    monkeypatch.setattr(tool_module, "baseline", unavailable)
    output = run(ctx)
    assert output.policy_version == "ppo-test-1" and output.compare_with_milp is None
    assert ctx.plans.get(output.new_plan_id).assignments


def test_corrupt_solver_result_is_never_persisted(monkeypatch):
    from garuda.optimization.retask_rolling_horizon.model import Solution

    ctx, _ = context()
    monkeypatch.setattr(
        tool_module,
        "baseline",
        lambda p: Solution("OPTIMAL", p.retask.plan.assignments, objective=0),
    )
    with pytest.raises(ToolInputError, match="Assignments do not match"):
        run(ctx)
    assert len(ctx.plans.items) == 1


def test_cancelled_worker_never_persists_draft(monkeypatch):
    ctx, _ = context()
    entered, release = ThreadEvent(), ThreadEvent()
    original = tool_module._compute

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(tool_module, "_compute", blocked)

    async def cancel():
        task = asyncio.create_task(rl_retask(request(), ctx))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    asyncio.run(cancel())  # drains the worker executor before assertions
    assert len(ctx.plans.items) == 1


def test_graph_uses_real_tool():
    pytest.importorskip("langgraph")
    from garuda.sim.rl_retask.graph import GRAPH

    ctx, _ = context()
    state = asyncio.run(
        GRAPH.ainvoke(
            {"payload": request().model_dump(mode="json")},
            {"configurable": {"ctx": ctx}},
        )
    )
    output = RlRetaskOutput.model_validate(state["output"])
    assert ctx.plans.get(output.new_plan_id).assignments[0].tail_no != "A1"


def test_public_contract_is_unchanged():
    assert set(RlRetaskInput.model_fields) == {"plan_id", "event", "deterministic"}
    assert RlRetaskInput.model_fields["deterministic"].default is True
    assert set(RlRetaskOutput.model_fields) == {
        "new_plan_id",
        "diff",
        "policy_version",
        "decision_latency_ms",
        "compare_with_milp",
    }
    assert (
        SPEC.name == "rl_retask"
        and SPEC.side_effects == "none"
        and SPEC.timeout_s == 20
    )


def test_real_maskable_ppo_saved_checkpoint(tmp_path):
    gym = pytest.importorskip("gymnasium")
    sb3 = pytest.importorskip("sb3_contrib")
    ctx, _ = context()
    ctx, prototype = with_policy(ctx, max_aircraft=4, max_missions=3, max_bases=3)
    decision = GarudaRetaskEnv(build_problem(request(), ctx), prototype)
    decision.action_masks()
    shape = decision.observation().shape

    class TrainingSpace(gym.Env):
        observation_space = gym.spaces.Box(
            -np.inf, np.inf, shape=shape, dtype=np.float32
        )
        action_space = gym.spaces.Discrete(decision.action_count)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return np.zeros(shape, dtype=np.float32), {}

        def step(self, action):
            return np.zeros(shape, dtype=np.float32), 0.0, True, False, {}

    model = sb3.MaskablePPO(
        "MlpPolicy",
        TrainingSpace(),
        n_steps=2,
        batch_size=2,
        policy_kwargs={"net_arch": [8, 8]},
        seed=42,
        device="cpu",
    )
    checkpoint = tmp_path / "policy.zip"
    model.save(checkpoint)
    loaded = sb3.MaskablePPO.load(checkpoint, device="cpu")
    ctx, _ = with_policy(ctx, loaded, max_aircraft=4, max_missions=3, max_bases=3)
    output = run(ctx)
    assert output.policy_version == "ppo-test-1"
    assert output.compare_with_milp["used_milp"] == 0
    assert output.compare_with_milp["constraint_violations"] == 0


def test_policy_budget_exhaustion_triggers_fallback(monkeypatch):
    model_module = importlib.import_module("garuda.sim.rl_retask.model")
    ctx, _ = context()
    ctx, _ = with_policy(ctx)
    monkeypatch.setattr(model_module, "perf_counter", lambda: float("inf"))
    assert run(ctx).compare_with_milp["used_milp"] == 1


def test_aircraft_overlap_mask_and_fallback_resource_checker():
    ctx, _ = context(missions=[mission("M1"), mission("M2")])
    ctx, artifact = with_policy(ctx, max_aircraft=4, max_missions=3)
    problem = build_problem(request(), ctx)
    env = GarudaRetaskEnv(problem, artifact)
    env.action_masks()
    env.step(1 + 1 * 3)  # A2/M1
    masks = env.action_masks()
    assert not masks[1 + 1 * 3 + 1]  # A2/M2 overlaps
    assert masks[1 + 2 * 3 + 1]  # spare A3/M2


@pytest.mark.parametrize("resource", ["stock", "crew", "capacity"])
def test_resource_unavailability_is_independently_validated(resource):
    ctx, scenario = context()
    if resource == "stock":
        for stock in scenario.stocks:
            stock.quantity = 0
    elif resource == "crew":
        scenario.crew = []
    else:
        ctx.providers.capacity.cap = lambda *args: 0
    ctx, _ = with_policy(ctx)
    output = run(ctx)
    assert not ctx.plans.get(output.new_plan_id).assignments
    assert output.compare_with_milp["constraint_violations"] == 0


def test_new_sam_invalidates_exposed_route():
    from garuda.schemas.threat import Threat

    ctx, scenario = context()
    ctx.plans.get("original").legs = [
        FlightLeg(mission_id="M1", tail_no="A1", waypoints=[])
    ]
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
    ctx, _ = with_policy(ctx)
    output = run(ctx, request("new_sam", "SAM"))
    child = ctx.plans.get(output.new_plan_id)
    assert not child.legs and child.kpis.risk == pytest.approx(0.8)


def test_tanker_loss_event_accepts_only_valid_receiver_selection():
    aar = mission("TANKER", type="AAR", required_capabilities=["AAR"], start=3)
    strike = mission("M1", start=4)
    tanker = aircraft("T1", type="IL-78", capabilities=["AAR"])
    ctx, _ = context(
        missions=[aar, strike],
        aircrafts=[aircraft("A1"), aircraft("A2"), tanker],
        assignments=[
            Assignment(
                tail_no="T1",
                config_id="SUPPORT",
                mission_id="TANKER",
                depart=aar.window_start,
                tot=aar.window_start,
            ),
            Assignment(
                tail_no="A1",
                config_id="C1",
                mission_id="M1",
                depart=strike.window_start,
                tot=strike.window_start,
            ),
        ],
    )
    # In this small fixture the receiver has no AAR requirement; tanker loss must
    # remove the failed tanker and preserve a valid independent receiver sortie.
    ctx, _ = with_policy(ctx)
    output = run(ctx, request("tanker_lost", "T1"))
    assert all(a.tail_no != "T1" for a in ctx.plans.get(output.new_plan_id).assignments)
    assert output.compare_with_milp["constraint_violations"] == 0


def test_checkpoint_deserialization_error_uses_fallback():
    ctx, _ = context()

    def load(name):
        raise RuntimeError("checkpoint could not be deserialized")

    assert (
        run(replace(ctx, models=SimpleNamespace(load=load))).policy_version
        == "milp-fallback"
    )
