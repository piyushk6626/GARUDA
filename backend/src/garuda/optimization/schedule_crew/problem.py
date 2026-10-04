"""Gather scheduling data through the existing context ports, without side effects."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from math import isfinite

from garuda.contracts.plan import PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.schedule_crew.schemas import ScheduleCrewInput
from garuda.queries.crew import AAR_WINDOW, NIGHT_WINDOW, QUALIFICATION_WINDOW
from garuda.schemas.crew import Crew

TICK = timedelta(microseconds=1)
DAY = timedelta(days=1) // TICK


@dataclass(frozen=True)
class Blocker:
    reason: str
    detail: str


@dataclass(frozen=True)
class CrewProblem:
    plan: PlanDraft
    crew: tuple[Crew, ...]
    # All times use integer microseconds relative to scenario_time.
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    candidates: tuple[tuple[int, ...], ...]
    blockers: tuple[dict[int, Blocker], ...]
    fatigue: tuple[float, ...]
    increments: tuple[float, ...]
    max_duty: int
    min_rest: int
    time_limit_s: int
    seed: int


def _aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ToolInputError(f"{name} must be timezone-aware")


def _current(moment: datetime | None, at: datetime, window: timedelta) -> bool:
    at = at.astimezone(timezone.utc)
    return moment is not None and at - window <= moment.astimezone(timezone.utc) <= at


def _blocker(member: Crew, slot: SortieSlot, fatigue: float, min_rest: float) -> Blocker | None:
    if member.role != slot.role or slot.aircraft_type not in member.qualifications:
        return Blocker("NO_QUALIFIED", "role or aircraft qualification does not match")
    if not _current(member.currency.by_type.get(slot.aircraft_type), slot.start, QUALIFICATION_WINDOW):
        return Blocker("NO_QUALIFIED", "aircraft currency is expired or missing")
    if slot.night and not _current(member.currency.night, slot.start, NIGHT_WINDOW):
        return Blocker("NO_QUALIFIED", "night currency is expired or missing")
    if slot.needs_aar and not _current(member.currency.aar, slot.start, AAR_WINDOW):
        return Blocker("NO_QUALIFIED", "AAR currency is expired or missing")
    if member.medical_status != "FIT":
        return Blocker("MEDICAL", f"medical status is {member.medical_status}")
    if member.leave_until is not None and member.leave_until > slot.start:
        return Blocker("MEDICAL", f"on leave until {member.leave_until.isoformat()}")
    if member.current_assignment is not None or member.duty_hours_24h > 0:
        # The frozen roster has no end time for an active duty/assignment.
        return Blocker("REST", "already assigned or on duty; release time is unknown")
    rest = member.rest_hours_since_last_duty + (
        slot.start.astimezone(timezone.utc) - member.updated_at.astimezone(timezone.utc)
    ).total_seconds() / 3600
    if rest < min_rest:
        return Blocker("REST", f"needs {min_rest - rest:.2f} h more rest")
    if fatigue > 0.7:
        return Blocker("REST", f"fatigue {fatigue:.3f} exceeds the 0.7 scheduling threshold")
    return None


def build_problem(payload: ScheduleCrewInput, ctx: ToolContext) -> CrewProblem:
    """Keep public schemas frozen; reject bad values here instead of changing them."""
    if not payload.plan_id.strip():
        raise ToolInputError("plan_id must not be empty")
    if not isfinite(payload.max_duty_hours) or not 0 < payload.max_duty_hours <= 24:
        raise ToolInputError("max_duty_hours must be finite and between 0 (exclusive) and 24")
    if not isfinite(payload.min_rest_hours) or payload.min_rest_hours < 0:
        raise ToolInputError("min_rest_hours must be finite and non-negative")
    if not 0 < payload.time_limit_s <= 40:
        raise ToolInputError("time_limit_s must be between 1 and the tool timeout of 40 seconds")
    max_duty = int(Decimal(str(payload.max_duty_hours)) * 3_600_000_000)
    min_rest = int((Decimal(str(payload.min_rest_hours)) * 3_600_000_000).to_integral_value(rounding=ROUND_CEILING))
    if min_rest > 2**60:
        raise ToolInputError("min_rest_hours exceeds the solver's supported time range")
    _aware(ctx.scenario_time, "scenario_time")
    try:
        plan = ctx.plans.get(payload.plan_id).model_copy(deep=True)
    except KeyError as exc:
        raise ToolInputError(f"unknown plan_id: {payload.plan_id}") from exc
    if plan.plan_id != payload.plan_id:
        raise ToolInputError("PlanStore returned a different plan_id")
    if plan.status != "DRAFT":
        raise ToolInputError("crew scheduling requires a DRAFT plan")
    if len({slot.slot_id for slot in plan.sortie_slots}) != len(plan.sortie_slots):
        raise ToolInputError("sortie slot IDs must be unique")
    for slot in plan.sortie_slots:
        _aware(slot.start, f"slot {slot.slot_id} start")
        _aware(slot.end, f"slot {slot.slot_id} end")
        if slot.end.astimezone(timezone.utc) <= slot.start.astimezone(timezone.utc):
            raise ToolInputError(f"slot {slot.slot_id} end must be after start")
        if slot.start.astimezone(timezone.utc) < ctx.scenario_time.astimezone(timezone.utc):
            raise ToolInputError(f"slot {slot.slot_id} starts before scenario_time")

    snapshot = ctx.scenario.snapshot()
    bases = None if payload.base_ids is None else set(payload.base_ids)
    if bases is not None:
        unknown = bases - set(ctx.scenario.base_ids())
        if unknown:
            raise ToolInputError(f"unknown base_ids: {', '.join(sorted(unknown))}")
    crew = tuple(sorted(
        (member.model_copy(deep=True) for member in snapshot.crew if bases is None or member.base_id in bases),
        key=lambda member: member.crew_id,
    ))
    if len({member.crew_id for member in crew}) != len(crew):
        raise ToolInputError("crew IDs must be unique")
    for member in crew:
        if member.updated_at.astimezone(timezone.utc) > ctx.scenario_time.astimezone(timezone.utc):
            raise ToolInputError(f"crew {member.crew_id} snapshot is after scenario_time")
    fatigue = tuple(float(ctx.providers.fatigue.fatigue(member.crew_id, ctx.scenario_time)) for member in crew)
    increments = tuple(float(ctx.providers.fatigue.phi(slot)) for slot in plan.sortie_slots)
    if any(not isfinite(value) or not 0 <= value <= 1 for value in fatigue):
        raise ToolInputError("FatigueProvider.fatigue must return finite scores between 0 and 1")
    if any(not isfinite(value) or not 0 <= value <= 1 for value in increments):
        raise ToolInputError("FatigueProvider.phi must return finite increments between 0 and 1")
    candidates, blockers = [], []
    for slot in plan.sortie_slots:
        blocked = {}
        eligible = []
        duration = (slot.end.astimezone(timezone.utc) - slot.start.astimezone(timezone.utc)) // TICK
        for k, member in enumerate(crew):
            blocker = _blocker(member, slot, fatigue[k], payload.min_rest_hours)
            if blocker is None and duration > max_duty:
                blocker = Blocker("REST", "sortie duration exceeds the rolling-24h duty limit")
            if blocker is None:
                eligible.append(k)
            else:
                blocked[k] = blocker
        candidates.append(tuple(eligible))
        blockers.append(blocked)
    return CrewProblem(
        plan=plan.model_copy(deep=True), crew=crew,
        starts=tuple((slot.start.astimezone(timezone.utc) - ctx.scenario_time.astimezone(timezone.utc)) // TICK
                     for slot in plan.sortie_slots),
        ends=tuple((slot.end.astimezone(timezone.utc) - ctx.scenario_time.astimezone(timezone.utc)) // TICK
                   for slot in plan.sortie_slots),
        candidates=tuple(candidates), blockers=tuple(blockers), fatigue=fatigue,
        increments=increments, max_duty=max_duty, min_rest=min_rest,
        time_limit_s=payload.time_limit_s, seed=ctx.seed,
    )


def duty_windows(problem: CrewProblem, indices: list[int]) -> list[tuple[tuple[int, int], ...]]:
    """Exact rolling duty: extrema occur when either window edge meets a slot edge.

    Weekly hours and brief/debrief buffers are absent from the frozen contracts;
    duty therefore uses the supplied sortie intervals, without invented history.
    """
    boundaries = {edge + offset for i in indices for edge in (problem.starts[i], problem.ends[i])
                  for offset in (0, DAY)}
    windows = set()
    for end in boundaries:
        terms = tuple((i, min(problem.ends[i], end) - max(problem.starts[i], end - DAY))
                      for i in indices if problem.starts[i] < end and problem.ends[i] > end - DAY)
        if terms:
            windows.add(terms)
    return sorted(windows)


def pairing_groups(problem: CrewProblem) -> list[list[int]]:
    groups: dict[tuple, list[int]] = {}
    for i, slot in enumerate(problem.plan.sortie_slots):
        key = (slot.mission_id, slot.tail_no, problem.starts[i], problem.ends[i])
        groups.setdefault(key, []).append(i)
    return [indices for indices in groups.values()
            if {problem.plan.sortie_slots[i].role for i in indices} >= {"PILOT", "WSO"}]
