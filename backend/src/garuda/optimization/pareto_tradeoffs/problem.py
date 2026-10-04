"""Prepare Pareto data without changing the frozen assignment contracts."""

from dataclasses import dataclass, replace
from types import SimpleNamespace

from garuda.contracts.plan import PlanDraft, SortieSlot
from garuda.contracts.tool import ToolInputError
from garuda.optimization.assign_missions.preprocess import (
    PreparedProblem,
    probability,
)
from garuda.optimization.assign_missions.preprocess import (
    build_problem as prepare_assignment,
)
from garuda.optimization.assign_missions.schemas import AssignMissionsInput


def assignment_key(a):
    return a.tail_no, a.config_id, a.mission_id, a.depart, a.tot


def crew_group(c, role):
    eligible, count = c.crew[role]
    a = c.assignment
    return a.tail_no, a.mission_id, a.depart, c.recover, role, eligible, count


def sortie_slot(c, role, seat=0):
    a = c.assignment
    return SortieSlot(
        slot_id=f"{a.mission_id}:{a.tail_no}:{a.depart.isoformat()}:{role}:{seat}",
        mission_id=a.mission_id,
        tail_no=a.tail_no,
        role=role,
        aircraft_type=c.aircraft_type,
        start=a.depart,
        end=c.recover,
        night=c.night,
    )


@dataclass
class ParetoProblem:
    base: PlanDraft
    assignment: PreparedProblem
    objectives: tuple[str, ...]
    n_points: int
    method: str
    time_limit_s: int
    seed: int
    munitions: dict[tuple, int]
    fatigue: dict[str, float]
    increments: dict[tuple, float]
    notes: list[str]


def build_problem(payload, ctx) -> ParetoProblem:
    if not payload.base_plan_id.strip():
        raise ToolInputError("base_plan_id must not be empty")
    if not payload.objectives or len(set(payload.objectives)) != len(
        payload.objectives
    ):
        raise ToolInputError("objectives must be a non-empty list without duplicates")
    if payload.n_points <= 0 or payload.time_limit_s_per_solve <= 0:
        raise ToolInputError("n_points and time_limit_s_per_solve must be positive")
    try:
        original = ctx.plans.get(payload.base_plan_id)
    except KeyError as exc:
        raise ToolInputError(f"Unknown base_plan_id: {payload.base_plan_id}") from exc
    if original is None or original.plan_id != payload.base_plan_id:
        raise ToolInputError("PlanStore did not return the requested base plan")
    base = original.model_copy(deep=True)
    keys = [assignment_key(a) for a in base.assignments]
    if len(set(keys)) != len(keys):
        raise ToolInputError("Base plan contains duplicate assignments")
    snapshot = ctx.scenario.snapshot()
    context = replace(ctx, scenario=SimpleNamespace(snapshot=lambda: snapshot))
    mission_ids = sorted(
        {a.mission_id for a in base.assignments}
        | {u.mission_id for u in base.unassigned}
    )
    request = AssignMissionsInput(
        scenario_time=base.scenario_time,
        time_limit_s=payload.time_limit_s_per_solve,
        mission_ids=mission_ids or None,
    )
    prepared = prepare_assignment(request, context)
    notes = []
    if base.assignments:
        exact = prepare_assignment(
            request.model_copy(update={"locked_assignments": base.assignments}), context
        )
        notes.extend(exact.lock_errors)
        # Keep sampled departures AND feasible exact base times, without locks.
        for key in exact.lock_keys:
            c = exact.candidates[key]
            if (
                key not in prepared.candidates
                or prepared.candidates[key].assignment != c.assignment
            ):
                unique = (*key[:3], int(c.assignment.depart.timestamp() * 1_000_000))
                prepared.candidates[unique] = replace(c, key=unique)
    categories = {w.weapon_code: w.category for w in snapshot.weapons}
    munitions = {
        k: sum(
            count for code, count in c.resources.items() if categories[code] != "PODS"
        )
        for k, c in prepared.candidates.items()
    }
    fatigue, increments = {}, {}
    if "fatigue" in payload.objectives:
        for c in prepared.candidates.values():
            for role, (eligible, _) in c.crew.items():
                g = crew_group(c, role)
                if g not in increments:
                    increments[g] = probability(
                        ctx.providers.fatigue.phi(sortie_slot(c, role)), "fatigue.phi"
                    )
                for member in eligible:
                    if member not in fatigue:
                        fatigue[member] = probability(
                            ctx.providers.fatigue.fatigue(member, base.scenario_time),
                            "fatigue.fatigue",
                        )
    return ParetoProblem(
        base,
        prepared,
        tuple(payload.objectives),
        payload.n_points,
        payload.method,
        payload.time_limit_s_per_solve,
        ctx.seed,
        munitions,
        fatigue,
        increments,
        notes,
    )
