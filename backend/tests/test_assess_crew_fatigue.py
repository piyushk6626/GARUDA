import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.plan import CrewAssignment, PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.ml.assess_crew_fatigue.model import (
    State, advance, circadian, initial_state, score, step,
)
from garuda.ml.assess_crew_fatigue.schemas import (
    AssessCrewFatigueInput, AssessCrewFatigueOutput, CrewFatigue,
)
from garuda.ml.assess_crew_fatigue.tool import SPEC, _band, assess_crew_fatigue
from garuda.schemas.crew import Crew, CrewDutyLog, Currency

T0 = datetime(2026, 6, 1, 11, 30, tzinfo=timezone.utc)  # 17:00 local peak


def crew(crew_id="C1", base_id="B1", duty=0, rest=12, fatigue=0.2, at=T0):
    return Crew(
        crew_id=crew_id, callsign=crew_id, role="PILOT", sqn_id="SQ1",
        base_id=base_id, primary_type="Su-30MKI",
        qualifications={"Su-30MKI": ["CAP"]}, currency=Currency(by_type={"Su-30MKI": at}),
        flight_hours_total=1000, flight_hours_last_30d=20,
        duty_hours_24h=duty, rest_hours_since_last_duty=rest,
        fatigue_score=fatigue, medical_status="FIT", updated_at=at,
    )


def slot(slot_id="S1", start=0, end=4, night=False):
    return SortieSlot(
        slot_id=slot_id, mission_id="M1", tail_no="SU1", role="PILOT",
        aircraft_type="Su-30MKI", start=T0 + timedelta(hours=start),
        end=T0 + timedelta(hours=end), night=night,
    )


def context(members=None, slots=None, assignments=None, logs=None):
    snapshot = SimpleNamespace(
        crew=members if members is not None else [crew()],
        crew_duty=logs or [],
        bases=[SimpleNamespace(base_id=base_id) for base_id in ("B1", "B2")],
    )
    plan = PlanDraft(
        plan_id="P1", scenario_time=T0, sortie_slots=slots or [],
        crew_assignments=assignments or [],
    )

    def get(plan_id):
        if plan_id != "P1":
            raise KeyError(plan_id)
        return plan

    ctx = ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: snapshot), plans=SimpleNamespace(get=get),
        models=None, providers=None, scenario_time=T0,
    )
    return ctx, snapshot, plan


def assess(ctx, **kwargs):
    return asyncio.run(assess_crew_fatigue(AssessCrewFatigueInput(**kwargs), ctx))


def assigned_slots(*slots):
    return [CrewAssignment(crew_id="C1", slot_id=item.slot_id, role="PILOT") for item in slots]


def test_contracts_remain_fixed():
    assert set(AssessCrewFatigueInput.model_fields) == {"crew_ids", "base_ids", "plan_id", "at_time"}
    assert set(AssessCrewFatigueOutput.model_fields) == {"items", "base_summary"}
    assert set(CrewFatigue.model_fields) == {"crew_id", "now", "projected_peak", "risk_band", "drivers"}
    assert SPEC.name == "assess_crew_fatigue"
    assert SPEC.side_effects == "none"
    assert SPEC.timeout_s == 10


def test_reservoir_equations_fractional_hours():
    state = State(reservoir=0.5)
    step(state, 0.5, duty=True)
    assert state.reservoir == pytest.approx(0.49)
    assert state.duty == 0.5
    step(state, 8, duty=False, asleep=True)
    assert state.reservoir == pytest.approx(1 - 0.51 * 0.9**8)


def test_canonical_wake_and_sleep_recovery():
    state = State()
    step(state, 24, duty=True)
    tired = score(state, T0)
    assert tired > 0.7
    step(state, 8, duty=False, asleep=True)
    assert score(state, T0) < 0.4
    assert score(state, T0) < tired


def test_more_duty_and_less_sleep_raise_fatigue():
    values = [score(initial_state(duty, 0, 0, T0), T0) for duty in (1, 6, 12, 18, 24)]
    assert values == sorted(values)
    state = State(reservoir=0.3)
    one, eight = replace(state), replace(state)
    step(one, 1, duty=False, asleep=True)
    step(eight, 8, duty=False, asleep=True)
    assert score(eight, T0) < score(one, T0)


def test_circadian_and_inertia():
    state = State(reservoir=0.7)
    assert score(state, T0 + timedelta(hours=12)) > score(state, T0)
    assert circadian(T0) == pytest.approx(1)
    assert circadian(T0.astimezone(timezone(timedelta(hours=5.5)))) == pytest.approx(1)
    step(state, 6.5, duty=False, asleep=True)
    asleep_score = score(state, T0)
    step(state, 0, duty=False)
    assert score(state, T0) == pytest.approx(asleep_score + 0.03)


@pytest.mark.parametrize("value,expected", [(0, "GREEN"), (0.3999, "GREEN"), (0.4, "AMBER"), (0.7, "AMBER"), (0.7001, "RED"), (1, "RED")])
def test_band_boundaries(value, expected):
    assert _band(value) == expected


@pytest.mark.parametrize("stored,expected", [(0.4, "AMBER"), (0.7, "AMBER"), (0.7001, "RED")])
def test_observed_band_boundaries_survive_model_inversion(stored, expected):
    ctx, _, _ = context([crew(fatigue=stored)])
    item = assess(ctx).items[0]
    assert item.now == stored
    assert item.risk_band == expected


def test_sleep_inertia_is_present_at_exact_wake_boundary():
    state = State(reservoir=0.5)
    advance(state, T0, T0 + timedelta(hours=6.5), duty=False)
    assert state.awake == 0
    assert not state.asleep
    assert score(state, T0) == pytest.approx(1 - state.reservoir * 1.05 + 0.03)


def test_sortie_interrupting_sleep_samples_immediate_inertia():
    state = State(reservoir=0.5, awake=0, woke=True, asleep=True)
    peak = advance(state, T0, T0 + timedelta(minutes=15), duty=True, peak_from=T0)
    assert peak == pytest.approx(1 - 0.5 * 1.05 + 0.03)


def test_end_to_end_filters_sorting_summaries_and_determinism():
    ctx, snapshot, plan = context([crew("C2", "B2", duty=24, rest=0), crew("C1")])
    before = [member.model_dump() for member in snapshot.crew], plan.model_dump()
    out = assess(ctx)
    assert [item.crew_id for item in out.items] == ["C1", "C2"]
    assert out.items[1].now > 0.7
    assert out.base_summary == {"B1": {"GREEN": 1, "AMBER": 0, "RED": 0}, "B2": {"GREEN": 0, "AMBER": 0, "RED": 1}}
    assert all(item.now == item.projected_peak for item in out.items)
    assert assess(ctx) == out
    assert [member.model_dump() for member in snapshot.crew] == before[0]
    assert plan.model_dump() == before[1]
    assert [item.crew_id for item in assess(ctx, crew_ids=["C2", "C2"]).items] == ["C2"]
    assert [item.crew_id for item in assess(ctx, base_ids=["B1"]).items] == ["C1"]
    assert not assess(ctx, crew_ids=["C1"], base_ids=["B2"]).items


@pytest.mark.parametrize("kwargs", [{"crew_ids": []}, {"base_ids": []}])
def test_empty_filters_select_nobody(kwargs):
    ctx, _, _ = context()
    assert assess(ctx, **kwargs) == AssessCrewFatigueOutput(items=[], base_summary={})


def test_empty_roster():
    ctx, _, _ = context([])
    assert not assess(ctx).items


def test_plan_projects_only_assigned_crew_and_bands_peak():
    duty_slot = slot(end=24, night=True)
    ctx, _, plan = context([crew(), crew("C2")], [duty_slot], assigned_slots(duty_slot))
    before = plan.model_dump()
    out = assess(ctx, plan_id="P1")
    assert out.items[0].now == assess(ctx).items[0].now
    assert out.items[0].projected_peak > 0.7
    assert out.items[0].risk_band == "RED"
    assert "planned_sorties" in out.items[0].drivers
    assert "night_sorties" in out.items[0].drivers
    assert out.items[1].projected_peak == out.items[1].now
    assert out.base_summary["B1"]["RED"] == 1
    assert plan.model_dump() == before


def test_adjacent_slots_preserve_time_on_task():
    long = slot(end=12)
    first, second = slot(end=6), slot("S2", start=6, end=12)
    ctx1, _, _ = context(slots=[long], assignments=assigned_slots(long))
    ctx2, _, _ = context(slots=[second, first], assignments=assigned_slots(first, second))
    assert assess(ctx1, plan_id="P1").items[0].projected_peak == pytest.approx(assess(ctx2, plan_id="P1").items[0].projected_peak)


def test_rest_between_sorties_reduces_projected_peak():
    first = slot(end=8)
    adjacent, rested = slot("S2", start=8, end=16), slot("S2", start=20, end=28)
    ctx1, _, _ = context(slots=[first, adjacent], assignments=assigned_slots(first, adjacent))
    ctx2, _, _ = context(slots=[first, rested], assignments=assigned_slots(first, rested))
    assert assess(ctx2, plan_id="P1").items[0].projected_peak < assess(ctx1, plan_id="P1").items[0].projected_peak


def test_fractional_slots_and_ongoing_slot():
    duty_slot = slot(start=-0.1, end=0.12345)
    ctx, _, _ = context(slots=[duty_slot], assignments=assigned_slots(duty_slot))
    item = assess(ctx, plan_id="P1").items[0]
    assert 0 <= item.now <= item.projected_peak <= 1


def test_completed_and_unassigned_slots_do_not_project():
    past, unassigned = slot(start=-6, end=-2), slot("S2", end=24)
    ctx, _, _ = context(slots=[past, unassigned], assignments=assigned_slots(past))
    assert assess(ctx, plan_id="P1") == assess(ctx)


def test_at_time_defaults_to_scenario_clock_and_equivalent_timezones():
    ctx, _, _ = context()
    assert assess(ctx) == assess(ctx, at_time=T0)
    assert assess(ctx) == assess(ctx, at_time=T0.astimezone(timezone(timedelta(hours=-7))))


def test_ongoing_duty_extrapolates_without_known_release():
    ctx, _, _ = context([crew(duty=12, rest=0)])
    assert assess(ctx, at_time=T0 + timedelta(hours=12)).items[0].now > assess(ctx).items[0].now


def test_observed_red_score_is_preserved_until_rest():
    ctx, _, _ = context([crew(fatigue=0.8, rest=24)])
    assert assess(ctx).items[0].now == pytest.approx(0.8)
    recovered = assess(ctx, at_time=T0 + timedelta(hours=6.5)).items[0]
    assert recovered.now < 0.7


def test_history_uses_latest_eligible_log_without_future_leakage():
    at = T0 - timedelta(hours=2)
    logs = [
        CrewDutyLog(time=at, crew_id="C1", duty_hours_24h=24, fatigue_score=0.9),
        CrewDutyLog(time=T0 + timedelta(hours=1), crew_id="C1", duty_hours_24h=0, fatigue_score=0),
    ]
    ctx, _, _ = context(logs=logs)
    out = assess(ctx, at_time=at)
    assert out.items[0].now >= 0.9 - 1e-12
    assert out.items[0].risk_band == "RED"
    logs.reverse()
    assert assess(ctx, at_time=at) == out


def test_newer_duty_log_overrides_roster():
    at = T0 + timedelta(hours=1)
    log = CrewDutyLog(time=at, crew_id="C1", duty_hours_24h=24, fatigue_score=0.9)
    ctx, _, _ = context(logs=[log])
    assert assess(ctx, at_time=at).items[0].now >= 0.9 - 1e-12


def test_conflicting_log_observations_are_rejected():
    at = T0 - timedelta(hours=1)
    logs = [CrewDutyLog(time=at, crew_id="C1", duty_hours_24h=1, fatigue_score=value)
            for value in (0.2, 0.8)]
    ctx, _, _ = context(logs=logs)
    with pytest.raises(ToolInputError, match="conflicting duty logs"):
        assess(ctx, at_time=at)


def test_duplicate_roster_and_mismatched_plan_are_rejected():
    ctx, _, _ = context([crew(), crew()])
    with pytest.raises(ToolInputError, match="crew IDs must be unique"):
        assess(ctx)
    ctx, _, plan = context()
    plan.plan_id = "other"
    with pytest.raises(ToolInputError, match="unknown plan_id"):
        assess(ctx, plan_id="P1")


def test_query_inside_plan_skips_scores_before_at_time():
    duty_slot = slot(end=24)
    ctx, _, _ = context(slots=[duty_slot], assignments=assigned_slots(duty_slot))
    item = assess(ctx, plan_id="P1", at_time=T0 + timedelta(hours=12)).items[0]
    assert 0 <= item.now <= item.projected_peak <= 1
    assert item.projected_peak > 0.7


def test_extreme_early_observation_has_actionable_error():
    at = datetime(1, 1, 1, tzinfo=timezone.utc)
    ctx, _, _ = context([crew(at=at)])
    with pytest.raises(ToolInputError, match="24 hours of model history"):
        assess(ctx, at_time=at)


@pytest.mark.parametrize("kwargs,match", [
    ({"crew_ids": ["missing"]}, "crew_ids"),
    ({"base_ids": ["missing"]}, "base_ids"),
    ({"plan_id": "missing"}, "plan_id"),
    ({"plan_id": " "}, "plan_id"),
    ({"at_time": T0.replace(tzinfo=None)}, "timezone-aware"),
    ({"at_time": T0 - timedelta(days=1)}, "no crew observation"),
])
def test_invalid_input_errors(kwargs, match):
    ctx, _, _ = context()
    with pytest.raises(ToolInputError, match=match):
        assess(ctx, **kwargs)


@pytest.mark.parametrize("case,match", [
    ("missing_slot", "unknown assigned slot"),
    ("missing_crew", "unknown assigned crew"),
    ("duplicate_assignment", "duplicate crew assignment"),
    ("duplicate_slot", "slot IDs must be unique"),
    ("overlap", "overlapping"),
    ("negative_duration", "end must be after start"),
    ("naive_time", "timezone-aware"),
])
def test_invalid_plan_errors(case, match):
    first = slot()
    ctx, _, plan = context(slots=[first], assignments=assigned_slots(first))
    if case == "missing_slot":
        plan.crew_assignments[0].slot_id = "missing"
    elif case == "missing_crew":
        plan.crew_assignments[0].crew_id = "missing"
    elif case == "duplicate_assignment":
        plan.crew_assignments *= 2
    elif case == "duplicate_slot":
        plan.sortie_slots *= 2
    elif case == "overlap":
        other = slot("S2", start=1, end=5)
        plan.sortie_slots.append(other)
        plan.crew_assignments.extend(assigned_slots(other))
    elif case == "negative_duration":
        plan.sortie_slots[0].end = first.start
    else:
        plan.sortie_slots[0].start = first.start.replace(tzinfo=None)
    with pytest.raises(ToolInputError, match=match):
        assess(ctx, plan_id="P1")


def test_sleep_boundaries_and_fractional_rest_terminate_and_stay_bounded():
    state = State(rest=23.99999999999)
    advance(state, T0, T0 + timedelta(hours=48.12345), duty=False)
    assert 0 <= score(state, T0) <= 1
