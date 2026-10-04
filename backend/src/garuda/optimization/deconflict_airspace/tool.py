import asyncio
from datetime import timedelta

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.optimization.deconflict_airspace.explain import explain
from garuda.optimization.deconflict_airspace.model import solve
from garuda.optimization.deconflict_airspace.problem import build_problem
from garuda.optimization.deconflict_airspace.schemas import (
    Conflict,
    DeconflictAirspaceInput,
    DeconflictAirspaceOutput,
    FlightAdjustment,
)

SPEC = ToolSpec(
    name="deconflict_airspace",
    description="Shift times/altitudes so flights respect separation and zone capacity.",
    input_model=DeconflictAirspaceInput,
    output_model=DeconflictAirspaceOutput,
    side_effects="draft",
    timeout_s=30,
)

STUB_OUTPUT = DeconflictAirspaceOutput(
    status="FEASIBLE",
    adjusted_flights=[FlightAdjustment(flight="M-1001", shift_min=5, altitude_block="FL250")],
    remaining_conflicts=[Conflict(flight_a="M-1001", flight_b="M-1002", zone="CTR-B1", detail="skeleton")],
    total_shift_min=5,
)


async def deconflict_airspace(payload: DeconflictAirspaceInput, ctx: ToolContext) -> DeconflictAirspaceOutput:
    """Deconflict the existing draft, validate, and persist all timing changes."""
    # Shared executor is a stub; keep CPU work off the event loop locally.
    problem = await asyncio.to_thread(build_problem, payload, ctx)
    solution = await asyncio.to_thread(solve, problem)
    output = explain(problem, solution)
    if solution.has_solution and output.adjusted_flights:
        if ctx.plans.get(payload.plan_id) != problem.plan:
            raise ToolInputError("plan changed during airspace deconfliction; retry with the current draft")
        draft = problem.plan.model_copy(deep=True)
        changes = {flight.key: (solution.shifts[index], solution.levels[index] * 100)
                   for index, flight in enumerate(problem.flights)}
        for assignment in draft.assignments:
            shift, _ = changes[assignment.mission_id, assignment.tail_no]
            assignment.depart += timedelta(minutes=shift)
            assignment.tot += timedelta(minutes=shift)
        for leg in draft.legs:
            shift, altitude = changes[leg.mission_id, leg.tail_no]
            for waypoint in leg.waypoints:
                waypoint.eta += timedelta(minutes=shift)
                waypoint.alt_ft = altitude
        for slot in draft.sortie_slots:
            shift, _ = changes[slot.mission_id, slot.tail_no]
            slot.start += timedelta(minutes=shift)
            slot.end += timedelta(minutes=shift)
        ctx.plans.put(draft)
    return output


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, deconflict_airspace)
