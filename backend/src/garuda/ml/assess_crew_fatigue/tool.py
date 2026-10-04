import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite

from garuda.contracts.plan import SortieSlot
from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.ml.assess_crew_fatigue.model import circadian, evaluate, inertia, initial_state
from garuda.ml.assess_crew_fatigue.schemas import AssessCrewFatigueInput, AssessCrewFatigueOutput, CrewFatigue

SPEC = ToolSpec(
    name="assess_crew_fatigue",
    description="Crew fatigue now and under a proposed plan.",
    input_model=AssessCrewFatigueInput,
    output_model=AssessCrewFatigueOutput,
    side_effects="none",
    timeout_s=10,
)

# Compatibility fixture for the separate graph scaffold; never returned here.
STUB_OUTPUT = AssessCrewFatigueOutput(
    items=[
        CrewFatigue(
            crew_id="C-001",
            now=0.3,
            projected_peak=0.45,
            risk_band="AMBER",
            drivers=["duty_hours"],
        )
    ],
    base_summary={"B1": {"GREEN": 4, "AMBER": 1, "RED": 0}},
)


async def assess_crew_fatigue(payload: AssessCrewFatigueInput, ctx: ToolContext) -> AssessCrewFatigueOutput:
    """Assess selected crew without modifying snapshots, plans, or providers."""
    return await asyncio.to_thread(_assess, payload, ctx)


def _utc(moment: datetime, label: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ToolInputError(f"{label} must be timezone-aware")
    return moment.astimezone(timezone.utc)


def _band(value: float) -> str:
    return "RED" if value > 0.7 else "AMBER" if value >= 0.4 else "GREEN"


def _assess(payload: AssessCrewFatigueInput, ctx: ToolContext) -> AssessCrewFatigueOutput:
    at = _utc(payload.at_time if payload.at_time is not None else ctx.scenario_time, "at_time")
    snapshot = ctx.scenario.snapshot()
    roster = {member.crew_id: member for member in snapshot.crew}
    if len(roster) != len(snapshot.crew):
        raise ToolInputError("crew IDs must be unique")
    for names, known, label in (
        (payload.crew_ids, set(roster), "crew_ids"),
        (payload.base_ids, {base.base_id for base in snapshot.bases}, "base_ids"),
    ):
        unknown = set(names or []) - known
        if unknown:
            raise ToolInputError(f"unknown {label}: {', '.join(sorted(unknown))}")
    members = sorted(
        (member for member in snapshot.crew
         if (payload.crew_ids is None or member.crew_id in payload.crew_ids)
         and (payload.base_ids is None or member.base_id in payload.base_ids)),
        key=lambda member: member.crew_id,
    )
    slots_by_crew: dict[str, list[SortieSlot]] = defaultdict(list)
    if payload.plan_id is not None:
        if not payload.plan_id.strip():
            raise ToolInputError("plan_id must not be empty")
        try:
            plan = ctx.plans.get(payload.plan_id)
        except (KeyError, FileNotFoundError) as exc:
            raise ToolInputError(f"unknown plan_id: {payload.plan_id}") from exc
        if plan is None or plan.plan_id != payload.plan_id:
            raise ToolInputError(f"unknown plan_id: {payload.plan_id}")
        slots = {}
        for slot in plan.sortie_slots:
            if slot.slot_id in slots:
                raise ToolInputError("sortie slot IDs must be unique")
            start, end = _utc(slot.start, "slot start"), _utc(slot.end, "slot end")
            if end <= start:
                raise ToolInputError(f"slot {slot.slot_id} end must be after start")
            slots[slot.slot_id] = slot.model_copy(update={"start": start, "end": end})
        assigned = set()
        for assignment in plan.crew_assignments:
            if assignment.crew_id not in roster:
                raise ToolInputError(f"unknown assigned crew_id: {assignment.crew_id}")
            if assignment.slot_id not in slots:
                raise ToolInputError(f"unknown assigned slot_id: {assignment.slot_id}")
            key = (assignment.crew_id, assignment.slot_id)
            if key in assigned:
                raise ToolInputError("duplicate crew assignment")
            assigned.add(key)
            slots_by_crew[assignment.crew_id].append(slots[assignment.slot_id])
        for crew_id, crew_slots in slots_by_crew.items():
            crew_slots.sort(key=lambda slot: (slot.start, slot.end, slot.slot_id))
            if any(left.end > right.start for left, right in zip(crew_slots, crew_slots[1:])):
                raise ToolInputError(f"overlapping sortie slots for crew {crew_id}")

    # Select the latest observation at or before the query; never use future logs.
    observations = {}
    selected = {member.crew_id for member in members}
    for log in snapshot.crew_duty:
        if log.crew_id not in selected:
            continue
        time = _utc(log.time, "crew duty log time")
        if time <= at:
            previous = observations.get(log.crew_id)
            if previous is None or time > previous[0]:
                observations[log.crew_id] = (time, log.duty_hours_24h, log.fatigue_score)
            elif time == previous[0] and previous[1:] != (log.duty_hours_24h, log.fatigue_score):
                raise ToolInputError(f"conflicting duty logs for crew {log.crew_id}")
    items, summary = [], {}
    for member in members:
        updated = _utc(member.updated_at, "crew updated_at")
        observation = observations.get(member.crew_id)
        if updated <= at and (observation is None or updated >= observation[0]):
            observed_at, duty, stored = updated, member.duty_hours_24h, member.fatigue_score
            rest = member.rest_hours_since_last_duty
        elif observation is not None:
            observed_at, duty, stored = observation
            # Logs omit rest duration. An off-duty observation starts a rest
            # opportunity; historical queries cannot borrow future roster rest.
            rest = 0.0
        else:
            raise ToolInputError(f"no crew observation at or before at_time for {member.crew_id}")
        if any(not isfinite(value) for value in (duty, rest, stored)) or not (
            0 <= duty <= 24 and rest >= 0 and 0 <= stored <= 1
        ):
            raise ToolInputError(f"invalid duty/rest/fatigue observation for {member.crew_id}")
        crew_slots = slots_by_crew[member.crew_id]
        try:
            state = initial_state(duty, rest, stored, observed_at)
        except OverflowError as exc:
            raise ToolInputError("crew observation must allow 24 hours of model history") from exc
        now, peak, current, projected = evaluate(state, observed_at, at, crew_slots)
        drivers = ["inferred_sleep_history"]
        if current.duty > 0:
            drivers.append("duty_hours")
        if current.awake >= 16 or projected.awake >= 16:
            drivers.append("time_awake")
        if circadian(at) < 0:
            drivers.append("circadian_low")
        if inertia(current) > 0.005:
            drivers.append("sleep_inertia")
        remaining = [slot for slot in crew_slots if slot.end > at]
        if remaining:
            drivers.append("planned_sorties")
            if any(slot.night for slot in remaining):
                drivers.append("night_sorties")
        band = _band(peak)
        items.append(CrewFatigue(
            crew_id=member.crew_id, now=now, projected_peak=peak,
            risk_band=band, drivers=drivers,
        ))
        counts = summary.setdefault(member.base_id, {"GREEN": 0, "AMBER": 0, "RED": 0})
        counts[band] += 1
    return AssessCrewFatigueOutput(items=items, base_summary=dict(sorted(summary.items())))


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assess_crew_fatigue)
