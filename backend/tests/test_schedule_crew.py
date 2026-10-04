"""Crew tool contract, solver rules, partial coverage, and draft persistence."""

import asyncio
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace

import pytest

from garuda.contracts.plan import CrewAssignment, PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.schedule_crew.explain import explain
from garuda.optimization.schedule_crew.model import CrewSolution
from garuda.optimization.schedule_crew.problem import build_problem
from garuda.optimization.schedule_crew.schemas import ScheduleCrewInput, ScheduleCrewOutput, SlotGap
from garuda.optimization.schedule_crew.tool import SPEC, schedule_crew
from garuda.schemas.crew import Crew, Currency

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def member(crew_id="C1", **updates):
    values = dict(
        crew_id=crew_id, callsign=crew_id, role="PILOT", sqn_id="SQ1", base_id="B1",
        primary_type="Su-30MKI", qualifications={"Su-30MKI": ["BVR", "AG"]},
        currency=Currency(by_type={"Su-30MKI": NOW - timedelta(days=1)}, night=NOW, aar=NOW),
        flight_hours_total=1000, flight_hours_last_30d=30, duty_hours_24h=0,
        rest_hours_since_last_duty=24, fatigue_score=0.2, medical_status="FIT",
        updated_at=NOW,
    )
    values.update(updates)
    return Crew(**values)


def slot(slot_id="S1", start=0, end=1, **updates):
    values = dict(slot_id=slot_id, mission_id="M1", tail_no="T1", role="PILOT",
                  aircraft_type="Su-30MKI", start=NOW + timedelta(hours=start),
                  end=NOW + timedelta(hours=end))
    values.update(updates)
    return SortieSlot(**values)


class Plans:
    def __init__(self, plan):
        self.plan = plan
        self.writes = []

    def get(self, plan_id):
        if plan_id != self.plan.plan_id:
            raise KeyError(plan_id)
        return self.plan

    def put(self, plan):
        self.writes.append(plan)
        self.plan = plan
        return plan.plan_id


class Scenario:
    def __init__(self, roster):
        self.roster = roster

    def snapshot(self):
        return SimpleNamespace(crew=self.roster)

    def base_ids(self):
        return ["B1", "B2"]

    def data_as_of(self):
        return NOW


class Fatigue:
    def __init__(self, roster):
        self.scores = {crew.crew_id: crew.fatigue_score for crew in roster}

    def fatigue(self, crew_id, at):
        assert at == NOW
        return self.scores[crew_id]

    def phi(self, sortie):
        return 0.05 + 0.05 * sortie.night


def context(roster, slots, **plan_updates):
    plan = PlanDraft(plan_id="P1", scenario_time=NOW, sortie_slots=slots, **plan_updates)
    return ToolContext(scenario=Scenario(roster), plans=Plans(plan), models=None,
                       providers=SimpleNamespace(fatigue=Fatigue(roster)), scenario_time=NOW)


def run(ctx, **inputs):
    return asyncio.run(schedule_crew(ScheduleCrewInput(plan_id="P1", **inputs), ctx))


def test_public_contract_is_unchanged():
    assert list(ScheduleCrewInput.model_fields) == [
        "plan_id", "base_ids", "max_duty_hours", "min_rest_hours", "time_limit_s",
    ]
    assert ScheduleCrewInput(plan_id="P1").model_dump() == dict(
        plan_id="P1", base_ids=None, max_duty_hours=14, min_rest_hours=12, time_limit_s=20,
    )
    assert list(ScheduleCrewOutput.model_fields) == [
        "status", "crew_assignments", "unfilled_slots", "fatigue_after", "kpis",
    ]
    assert list(CrewAssignment.model_fields) == ["crew_id", "slot_id", "role"]
    assert list(SlotGap.model_fields) == ["slot_id", "reason", "detail"]
    assert SPEC.name == "schedule_crew" and SPEC.timeout_s == 40 and SPEC.side_effects == "draft"


def test_fills_actual_plan_uses_provider_and_saves_a_copy():
    ctx = context([member("fresh", fatigue_score=0.1), member("tired", fatigue_score=0.6)], [slot()])
    original = ctx.plans.plan
    ctx.providers.fatigue.scores["fresh"] = 0.05
    output = run(ctx)
    assert output.status == "OPTIMAL"
    assert output.crew_assignments == [CrewAssignment(crew_id="fresh", slot_id="S1", role="PILOT")]
    assert output.fatigue_after == {"fresh": 0.1, "tired": 0.6}
    assert output.unfilled_slots == []
    assert output.kpis["filled_slots"] == 1
    assert ctx.plans.plan.crew_assignments == output.crew_assignments
    assert original.crew_assignments == []
    assert ctx.plans.plan.sortie_slots == original.sortie_slots
    assert ctx.scenario.roster[0].fatigue_score == 0.1
    assert ScheduleCrewOutput.model_validate_json(output.model_dump_json()) == output


@pytest.mark.parametrize("updates,reason", [
    ({"role": "WSO"}, "NO_QUALIFIED"),
    ({"medical_status": "GROUNDED"}, "MEDICAL"),
    ({"medical_status": "LIMITED"}, "MEDICAL"),
    ({"leave_until": NOW + timedelta(hours=2)}, "MEDICAL"),
    ({"current_assignment": "M9"}, "REST"),
    ({"duty_hours_24h": 10, "rest_hours_since_last_duty": 0}, "REST"),
    ({"fatigue_score": 0.7001}, "REST"),
    ({"currency": Currency(by_type={"Su-30MKI": NOW - timedelta(days=91)})}, "NO_QUALIFIED"),
])
def test_blockers_produce_contract_reason_codes(updates, reason):
    output = run(context([member(**updates)], [slot()]))
    assert output.status == "INFEASIBLE"
    assert output.crew_assignments == []
    assert output.unfilled_slots[0].reason == reason
    assert output.unfilled_slots[0].detail
    assert output.kpis["overload_hours"] == 0


@pytest.mark.parametrize("flag,currency", [("night", "night"), ("needs_aar", "aar")])
def test_feature_currency(flag, currency):
    crew = member()
    crew.currency = crew.currency.model_copy(update={currency: None})
    output = run(context([crew], [slot(**{flag: True})]))
    assert output.unfilled_slots[0].reason == "NO_QUALIFIED"
    assert currency.lower() in output.unfilled_slots[0].detail.lower()


def test_currency_is_checked_at_sortie_time_and_exact_boundary():
    currency = Currency(by_type={"Su-30MKI": NOW - timedelta(days=90)})
    crew = member(currency=currency, fatigue_score=0.7)
    assert run(context([crew], [slot()])).kpis["filled_slots"] == 1
    assert run(context([crew], [slot(start=1, end=2)])).unfilled_slots[0].reason == "NO_QUALIFIED"


def test_base_filter_and_empty_selection_are_distinct_from_none():
    roster = [member("C1", base_id="B1"), member("C2", base_id="B2")]
    assert run(context(roster, [slot()]), base_ids=["B2"]).crew_assignments[0].crew_id == "C2"
    assert run(context(roster, [slot()]), base_ids=[]).unfilled_slots[0].reason == "NO_QUALIFIED"
    with pytest.raises(ToolInputError, match="unknown base"):
        run(context(roster, [slot()]), base_ids=["BOGUS"])


def test_initial_rest_projects_forward_and_reports_missing_hours():
    crew = member(rest_hours_since_last_duty=12)
    output = run(context([crew], [slot(start=1.5, end=2.5)]), min_rest_hours=14)
    assert output.unfilled_slots[0].reason == "REST"
    assert "0.50 h more rest" in output.unfilled_slots[0].detail
    assert run(context([crew], [slot(start=2, end=3)]), min_rest_hours=14).kpis["filled_slots"] == 1


def test_overlap_and_minimum_rest_are_hard_constraints():
    crew = [member()]
    assert run(context(crew, [slot(), slot("S2", start=0.5, end=1.5)])).kpis["filled_slots"] == 1
    assert run(context(crew, [slot(), slot("S2", start=12.999, end=14)])).kpis["filled_slots"] == 1
    assert run(context(crew, [slot(), slot("S2", start=13, end=14)])).kpis["filled_slots"] == 2


def test_rolling_duty_caps_and_resets():
    crew = [member()]
    output = run(context(crew, [slot(start=0, end=8), slot("S2", start=12, end=20)]), min_rest_hours=0)
    assert output.status == "INFEASIBLE" and output.kpis["filled_slots"] == 1
    assert "rolling-24h duty" in output.unfilled_slots[0].detail
    output = run(context(crew, [slot(start=0, end=12), slot("S2", start=23, end=27)]), min_rest_hours=0)
    # Maximum rolling duty is 13h despite 16h of total flying across the horizon.
    assert output.kpis["filled_slots"] == 2
    output = run(context(crew, [slot(start=0, end=10), slot("S2", start=34, end=44)]))
    assert output.kpis["filled_slots"] == 2


def test_duty_limit_never_uses_overload_slack():
    output = run(context([member()], [slot(end=15)]))
    assert output.status == "INFEASIBLE" and output.crew_assignments == []
    assert "duty limit" in output.unfilled_slots[0].detail


def test_pairing_chooses_a_common_squadron():
    roster = [member("P1", sqn_id="SQ1", fatigue_score=0.1),
              member("P2", sqn_id="SQ2", fatigue_score=0.2), member("W1", role="WSO", sqn_id="SQ2")]
    output = run(context(roster, [slot(), slot("S2", role="WSO")]))
    assert {a.crew_id for a in output.crew_assignments} == {"P2", "W1"}
    assert output.status == "OPTIMAL"


def test_unpairable_seats_return_a_valid_partial_schedule():
    roster = [member("P1", sqn_id="SQ1"), member("W1", role="WSO", sqn_id="SQ2")]
    ctx = context(roster, [slot(), slot("S2", role="WSO")])
    output = run(ctx)
    assert output.status == "INFEASIBLE" and len(output.crew_assignments) == 1
    assert output.unfilled_slots[0].reason == "NO_QUALIFIED"
    assert "squadron" in output.unfilled_slots[0].detail
    assert ctx.plans.plan.crew_assignments == output.crew_assignments


def test_maximum_coverage_beats_greedy_fatigue_choice():
    flexible = member("flex", fatigue_score=0.1)
    restricted = member("restricted", fatigue_score=0.3)
    restricted.currency = restricted.currency.model_copy(update={"night": None})
    output = run(context([flexible, restricted], [slot(), slot("S2", night=True)]))
    assert {a.slot_id: a.crew_id for a in output.crew_assignments} == {"S1": "restricted", "S2": "flex"}


def test_balances_equal_fatigue_roster_and_is_deterministic():
    ctx = context([member("C1"), member("C2")], [slot(), slot("S2", start=13, end=14)])
    first, second = run(ctx), run(ctx)
    assert first == second
    assert len({a.crew_id for a in first.crew_assignments}) == 2
    assert first.kpis["flight_hours_imbalance"] == 0


def test_empty_plan_and_roster():
    output = run(context([], []))
    assert output.status == "OPTIMAL" and output.kpis["coverage_pct"] == 100
    assert output.fatigue_after == {} and output.crew_assignments == []
    output = run(context([], [slot()]))
    assert output.status == "INFEASIBLE" and output.unfilled_slots[0].reason == "NO_QUALIFIED"


@pytest.mark.parametrize("inputs", [
    {"max_duty_hours": 0}, {"max_duty_hours": -1}, {"max_duty_hours": float("nan")},
    {"min_rest_hours": -1}, {"min_rest_hours": float("inf")}, {"time_limit_s": 0},
    {"time_limit_s": 41},
])
def test_invalid_options_do_not_write(inputs):
    ctx = context([member()], [slot()])
    with pytest.raises(ToolInputError):
        run(ctx, **inputs)
    assert ctx.plans.writes == []


@pytest.mark.parametrize("slots", [
    [slot(), slot()], [slot(start=2, end=1)], [slot(start=-1, end=1)],
    [slot().model_copy(update={"start": NOW.replace(tzinfo=None)})],
])
def test_invalid_slots_do_not_write(slots):
    ctx = context([member()], slots)
    with pytest.raises(ToolInputError):
        run(ctx)
    assert ctx.plans.writes == []


def test_missing_or_non_draft_plan_is_actionable():
    ctx = context([member()], [slot()])
    with pytest.raises(ToolInputError, match="unknown plan_id"):
        asyncio.run(schedule_crew(ScheduleCrewInput(plan_id="missing"), ctx))
    ctx.plans.plan.status = "APPROVED"
    with pytest.raises(ToolInputError, match="DRAFT"):
        run(ctx)


def test_unknown_solve_does_not_overwrite_existing_assignments(monkeypatch):
    import importlib
    module = importlib.import_module("garuda.optimization.schedule_crew.tool")

    async def unknown(*args):
        return CrewSolution("UNKNOWN", (), False, False)

    monkeypatch.setattr(module, "run_cpu", unknown)
    previous = [CrewAssignment(crew_id="C1", slot_id="S1", role="PILOT")]
    ctx = context([member()], [slot()], crew_assignments=previous)
    output = run(ctx)
    assert output.status == "UNKNOWN" and output.kpis["coverage_proven"] == 0
    assert ctx.plans.plan.crew_assignments == previous and ctx.plans.writes == []


def test_invalid_worker_schedule_is_rejected_before_write(monkeypatch):
    import importlib
    module = importlib.import_module("garuda.optimization.schedule_crew.tool")

    async def invalid(*args):
        return CrewSolution("OPTIMAL", ((0, 0), (1, 0)), True, True)

    monkeypatch.setattr(module, "run_cpu", invalid)
    ctx = context([member()], [slot(), slot("S2")])
    with pytest.raises(RuntimeError, match="overlap"):
        run(ctx)
    assert ctx.plans.writes == []


def test_no_infeasibility_claim_for_time_limited_partial_solution():
    problem = build_problem(ScheduleCrewInput(plan_id="P1"), context([member()], [slot(), slot("S2")]))
    output = explain(problem, CrewSolution("FEASIBLE", ((0, 0),), True, False))
    assert output.status == "FEASIBLE" and output.kpis["coverage_proven"] == 0


def test_changed_plan_is_not_overwritten(monkeypatch):
    import importlib
    module = importlib.import_module("garuda.optimization.schedule_crew.tool")
    ctx = context([member()], [slot()])

    async def changed(*args):
        ctx.plans.plan.status = "AWAITING_APPROVAL"
        return CrewSolution("OPTIMAL", ((0, 0),), True, True)

    monkeypatch.setattr(module, "run_cpu", changed)
    with pytest.raises(ToolInputError, match="plan changed"):
        run(ctx)
    assert ctx.plans.writes == []


def test_result_does_not_alias_stored_draft():
    ctx = context([member()], [slot()])
    output = run(ctx)
    output.crew_assignments[0].crew_id = "changed-by-caller"
    assert ctx.plans.plan.crew_assignments[0].crew_id == "C1"


def test_solver_runs_off_the_event_loop(monkeypatch):
    import importlib
    import threading
    module = importlib.import_module("garuda.optimization.schedule_crew.tool")
    caller_thread = threading.get_ident()
    real_solve = module.solve
    worker_threads = []

    def check_thread(problem):
        worker_threads.append(threading.get_ident())
        return real_solve(problem)

    monkeypatch.setattr(module, "solve", check_thread)
    run(context([member()], [slot()]))
    assert worker_threads and worker_threads[0] != caller_thread


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_fatigue_provider_values_fail_without_writing(score):
    ctx = context([member()], [slot()])
    ctx.providers.fatigue.scores["C1"] = score
    with pytest.raises(ToolInputError, match="FatigueProvider"):
        run(ctx)
    assert ctx.plans.writes == []


def test_small_schedules_match_exhaustive_coverage_and_replay_rules():
    """Independent exhaustive oracle catches coverage and constraint mistakes."""
    import random
    rng = random.Random(42)
    for case in range(15):
        roster = [member("C1"), member("C2")]
        slots = [slot(f"S{i}", start=(start := rng.randrange(0, 40)), end=start + rng.randrange(1, 9))
                 for i in range(5)]
        ctx = context(roster, slots)
        rest = rng.choice([0, 4, 12])
        duty = rng.choice([8, 14])

        def valid(choices):
            for k in range(2):
                intervals = sorted((s.start, s.end) for s, choice in zip(slots, choices) if choice == k)
                if any(b[0] - a[1] < timedelta(hours=rest) for a, b in zip(intervals, intervals[1:])):
                    return False
                # Scan half-hour windows independently of the solver's edge construction.
                for half_hour in range(-48, 140):
                    end = NOW + timedelta(minutes=30 * half_hour)
                    start = end - timedelta(hours=24)
                    total = sum(max(0, (min(b, end) - max(a, start)).total_seconds()) for a, b in intervals)
                    if total > duty * 3600:
                        return False
            return True

        best = max(sum(k >= 0 for k in choices) for choices in product([-1, 0, 1], repeat=5) if valid(choices))
        output = run(ctx, max_duty_hours=duty, min_rest_hours=rest)
        assert output.kpis["filled_slots"] == best, case
        chosen = {a.slot_id: int(a.crew_id[1:]) - 1 for a in output.crew_assignments}
        assert valid([chosen.get(s.slot_id, -1) for s in slots]), case
