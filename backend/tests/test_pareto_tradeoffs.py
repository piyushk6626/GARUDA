"""Known frontier, hard feasibility, immutable contracts and tool integration."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import combinations
from types import SimpleNamespace

import pytest
from garuda.contracts.plan import Assignment, PlanDraft
from garuda.contracts.tool import ToolInputError
from garuda.optimization.assign_missions.preprocess import Candidate, PreparedProblem
from garuda.optimization.assign_missions.validate import crew_witness, validate
from garuda.optimization.pareto_tradeoffs.baseline import baseline
from garuda.optimization.pareto_tradeoffs.explain import draft, explain, representatives
from garuda.optimization.pareto_tradeoffs.model import (
    Point,
    evaluate,
    nondominated,
    solve,
)
from garuda.optimization.pareto_tradeoffs.problem import ParetoProblem, build_problem
from garuda.optimization.pareto_tradeoffs.schemas import (
    ParetoTradeoffsInput,
    ParetoTradeoffsOutput,
)
from garuda.optimization.pareto_tradeoffs.tool import SPEC, pareto_tradeoffs

T = datetime(2026, 10, 4, 4, tzinfo=timezone.utc)


@pytest.fixture
def problem():
    prepared = PreparedProblem(seed=42)
    for i, (value, risk) in enumerate(((25, 0.1), (45, 0.5), (100, 0.9))):
        mid = f"M{i}"
        prepared.missions[mid] = SimpleNamespace(
            value=value,
            min_aircraft=1,
            max_aircraft=1,
            dependencies=[],
            duration_min=30,
        )
        key = "A", "CFG", mid, 0
        a = Assignment(
            tail_no="A",
            config_id="CFG",
            mission_id=mid,
            depart=T,
            tot=T + timedelta(minutes=10),
        )
        prepared.candidates[key] = Candidate(
            key,
            a,
            "B",
            0,
            T + timedelta(hours=1),
            T + timedelta(hours=2),
            1,
            {"BOMB": 1},
            0.8,
            risk,
            {"PILOT": (("C",), 1)},
            aircraft_type="Tejas",
        )
    prepared.inspection = {"A": 10}
    prepared.capacity = {("B", 0): 3}
    prepared.stock = {("B", "BOMB"): 3}
    base = PlanDraft(
        plan_id="base",
        scenario_time=T,
        assignments=[prepared.candidates["A", "CFG", "M2", 0].assignment],
    )
    return ParetoProblem(
        base,
        prepared,
        ("value", "risk"),
        5,
        "epsilon",
        3,
        42,
        {k: 1 for k in prepared.candidates},
        {},
        {},
        [],
    )


def frontier_vectors(result):
    return {
        (p.objectives["value"], round(p.objectives["risk"], 8)) for p in result.points
    }


def test_epsilon_finds_unsupported_nonconvex_point_and_matches_enumeration(problem):
    result = solve(problem)
    oracle = []
    keys = list(problem.assignment.candidates)
    for n in range(len(keys) + 1):
        for selection in combinations(keys, n):
            crew = crew_witness([problem.assignment.candidates[k] for k in selection])
            if crew is not None and not validate(problem.assignment, selection, crew):
                oracle.append(
                    Point(list(selection), crew, evaluate(problem, selection, crew))
                )
    assert frontier_vectors(result) == frontier_vectors(
        SimpleNamespace(points=nondominated(oracle, problem.objectives))
    )
    assert frontier_vectors(result) == {(0, 0), (25, 0.1), (45, 0.5), (100, 0.9)}
    assert result.ideal == {"value": 100, "risk": 0}
    assert result.nadir == {"value": 0, "risk": 0.9}
    for point in result.points:
        assert not validate(problem.assignment, point.selected, point.crew)


def test_weighted_method_only_finds_supported_points(problem):
    result = solve(replace(problem, method="weighted"))
    assert (45, 0.5) not in frontier_vectors(result)
    assert (100, 0.9) in frontier_vectors(result)
    assert (0, 0) in frontier_vectors(result)


def test_requested_cost_only_and_single_objective(problem):
    result = solve(replace(problem, objectives=("risk", "munitions")))
    assert len(result.points) == 1
    assert result.points[0].objectives == {"risk": 0, "munitions": 0}
    result = solve(replace(problem, objectives=("value",)))
    assert result.points[0].objectives == {"value": 100}


def test_disruption_retains_exact_base_and_counts_changes(problem):
    result = solve(replace(problem, objectives=("value", "disruption")))
    assert len(result.points) == 1
    assert result.points[0].objectives == {"value": 100, "disruption": 0}


def test_constraints_and_extensions_survive_pareto_search(problem):
    problem.assignment.extensions.append(
        lambda lp, x, _: lp.addConstraint(x["A", "CFG", "M2", 0] == 0)
    )
    result = solve(problem)
    assert max(p.objectives["value"] for p in result.points) == 45
    problem.assignment.stock["B", "BOMB"] = 0
    assert frontier_vectors(solve(problem)) == {(0, 0)}


def test_fatigue_is_max_accumulated_active_crew_score(problem):
    from garuda.optimization.pareto_tradeoffs.problem import crew_group

    problem.objectives = ("value", "fatigue")
    problem.fatigue = {"C": 0.6}
    problem.increments = {
        crew_group(c, "PILOT"): 0.5 for c in problem.assignment.candidates.values()
    }
    result = solve(problem)
    assert {
        (p.objectives["value"], p.objectives["fatigue"]) for p in result.points
    } == {(0, 0), (100, 1)}


def test_fatigue_balances_repeated_duty_between_crew(problem):
    from garuda.optimization.pareto_tradeoffs.problem import crew_group

    problem.objectives = ("value", "fatigue")
    old = list(problem.assignment.candidates.values())[:2]
    candidates = {}
    for i, c in enumerate(old):
        shift = timedelta(hours=i * 24)
        a = c.assignment.model_copy(
            update={
                "depart": c.assignment.depart + shift,
                "tot": c.assignment.tot + shift,
            }
        )
        key = (*c.key[:3], i * 4)
        candidates[key] = replace(
            c,
            key=key,
            assignment=a,
            bucket=i * 4,
            recover=c.recover + shift,
            ready=c.ready + shift,
            crew={"PILOT": (("C", "D"), 1)},
        )
    problem.assignment.candidates = candidates
    problem.assignment.missions = {
        c.assignment.mission_id: problem.assignment.missions[c.assignment.mission_id]
        for c in candidates.values()
    }
    problem.assignment.capacity = {("B", 0): 1, ("B", 4): 1}
    problem.munitions = dict.fromkeys(candidates, 1)
    problem.fatigue = {"C": 0.1, "D": 0.1}
    problem.increments = {crew_group(c, "PILOT"): 0.2 for c in candidates.values()}
    result = solve(problem)
    richest = max(result.points, key=lambda p: p.objectives["value"])
    assert richest.objectives == pytest.approx({"value": 70, "fatigue": 0.3})
    assert {m for members in richest.crew.values() for m in members} == {"C", "D"}
    assert not validate(problem.assignment, richest.selected, richest.crew)


def test_all_objectives_are_jointly_feasible(problem):
    from garuda.optimization.pareto_tradeoffs.problem import crew_group

    problem.objectives = ("value", "risk", "munitions", "fatigue", "disruption")
    problem.fatigue = {"C": 0.1}
    problem.increments = {
        crew_group(c, "PILOT"): 0.1 for c in problem.assignment.candidates.values()
    }
    result = solve(problem)
    assert len(result.points) > 1
    for point in result.points:
        assert set(point.objectives) == set(problem.objectives)
        assert not validate(problem.assignment, point.selected, point.crew)
    assert result.points == nondominated(result.points, problem.objectives)


def test_infeasible_epsilon_is_skipped_and_noted(problem):
    from time import perf_counter

    from garuda.optimization.pareto_tradeoffs.model import _attempt

    notes = []
    point = _attempt(
        problem,
        {"value": 1},
        {"risk": -1},
        {"value": 0, "risk": 0},
        {"value": 1, "risk": 1},
        None,
        perf_counter() + 3,
        notes,
    )
    assert point is None
    assert "Infeasible epsilon/anchor solve skipped" in notes


def test_degenerate_and_empty_frontier(problem):
    problem.assignment.candidates.clear()
    result = solve(problem)
    assert len(result.points) == 1
    assert result.points[0].objectives == {"value": 0, "risk": 0}
    output = explain(problem, result)
    assert output.recommendation == "Balanced"


def test_infeasible_model_is_actionable(problem):
    problem.assignment.extensions.append(
        lambda lp, x, _: lp.addConstraint(sum(x.values()) >= 5)
    )
    with pytest.raises(ToolInputError, match="No feasible"):
        solve(problem)


def test_representatives_ids_and_diffs_are_reproducible(problem):
    before = problem.base.model_dump()
    first = explain(problem, solve(problem))
    second = explain(problem, solve(problem))
    assert first == second
    assert problem.base.model_dump() == before
    assert len(first.options) == 4
    assert len({o.plan_id for o in first.options}) == 4
    assert any("costs" in s for o in first.options for s in o.key_differences)
    problem.n_points = 1
    limited = explain(problem, solve(problem))
    assert len(limited.options) == 1
    assert limited.options[0].label == limited.recommendation == "Balanced"


def test_medoids_keep_knee_and_limit_diverse_representatives():
    points = [Point([], {}, {"value": i, "risk": i * i}) for i in range(10)]
    selected, knee = representatives(points, ("value", "risk"), 5)
    assert len(set(selected)) == 5
    assert knee in selected
    assert 0 in selected and 9 in selected


def test_greedy_comparison_is_feasible(problem):
    point = baseline(problem)
    assert point is not None
    assert not validate(problem.assignment, point.selected, point.crew)


def test_nsga2_optional_dependency_is_actionable_or_seeded(problem):
    import importlib.util

    p = replace(problem, method="nsga2")
    if importlib.util.find_spec("pymoo") is None:
        with pytest.raises(ToolInputError, match="garuda\\[moo\\]"):
            solve(p)
    else:
        first, second = solve(p), solve(p)
        assert frontier_vectors(first) == frontier_vectors(second)
        assert (100, 0.9) in frontier_vectors(first)
        for point in first.points:
            assert not validate(p.assignment, point.selected, point.crew)


@pytest.fixture
def ctx():
    # Reuse a complete scenario fixture; no production context is implemented here.
    from test_assign_missions import ctx as assignment_context

    context = assignment_context.__wrapped__()
    context.plans.put(PlanDraft(plan_id="base", scenario_time=context.scenario_time))
    return context


def test_async_tool_reads_only_and_preserves_schemas(ctx):
    before = ctx.plans.items.copy()
    output = asyncio.run(
        pareto_tradeoffs(ParetoTradeoffsInput(base_plan_id="base"), ctx)
    )
    assert set(output.model_dump()) == {"options", "ideal", "nadir", "recommendation"}
    assert set(ParetoTradeoffsInput.model_fields) == {
        "base_plan_id",
        "objectives",
        "n_points",
        "method",
        "time_limit_s_per_solve",
    }
    assert SPEC.input_model is ParetoTradeoffsInput
    assert SPEC.output_model is ParetoTradeoffsOutput
    assert SPEC.side_effects == "none" and SPEC.timeout_s == 120
    assert ctx.plans.items == before
    assert len(output.options) == 2
    assert set(output.options[0].objectives) == {"value", "risk"}


def test_drafts_have_valid_crew_seats_and_kpis(ctx):
    problem = build_problem(
        ParetoTradeoffsInput(base_plan_id="base", objectives=["value", "fatigue"]), ctx
    )
    result = solve(problem)
    for point in result.points:
        option = draft(problem, point)
        assert len(option.crew_assignments) == len(option.sortie_slots)
        assert {c.slot_id for c in option.crew_assignments} == {
            s.slot_id for s in option.sortie_slots
        }
        assert option.parent_plan_id == "base" and option.status == "DRAFT"
        assert option.kpis.sorties == len(option.assignments)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"base_plan_id": ""}, "must not be empty"),
        ({"base_plan_id": "missing"}, "Unknown base_plan_id"),
        ({"objectives": []}, "non-empty"),
        ({"objectives": ["risk", "risk"]}, "duplicates"),
        ({"n_points": 0}, "positive"),
        ({"time_limit_s_per_solve": 0}, "positive"),
    ],
)
def test_bad_inputs_are_rejected_without_schema_changes(ctx, changes, match):
    payload = ParetoTradeoffsInput(base_plan_id="base").model_copy(update=changes)
    with pytest.raises(ToolInputError, match=match):
        asyncio.run(pareto_tradeoffs(payload, ctx))


def test_exact_base_departures_remain_candidates(ctx):
    from garuda.optimization.assign_missions.schemas import AssignMissionsInput
    from garuda.optimization.assign_missions.tool import assign_missions

    assigned = asyncio.run(
        assign_missions(AssignMissionsInput(scenario_time=ctx.scenario_time), ctx)
    )
    base = ctx.plans.get(assigned.plan_id)
    base.assignments[0].depart += timedelta(minutes=5)
    base.assignments[0].tot += timedelta(minutes=5)
    p = build_problem(
        ParetoTradeoffsInput(base_plan_id=base.plan_id, objectives=["disruption"]), ctx
    )
    point = solve(p).points[0]
    assert point.objectives == {"disruption": 0}
    assert draft(p, point).assignments == base.assignments
