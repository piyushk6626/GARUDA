from datetime import timedelta

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.schedule_tanker_aar.explain import explain
from garuda.optimization.schedule_tanker_aar.model import solve
from garuda.optimization.schedule_tanker_aar.problem import build_problem
from garuda.optimization.schedule_tanker_aar.schemas import (
    AarStop,
    RefuelEvent,
    ScheduleTankerAarInput,
    ScheduleTankerAarOutput,
    TankerRoute,
)
from garuda.tools.stubs import STUB_TIME, fixed

SPEC = ToolSpec(
    name="schedule_tanker_aar",
    description="Schedule tankers to meet receivers at air refuelling points.",
    input_model=ScheduleTankerAarInput,
    output_model=ScheduleTankerAarOutput,
    side_effects="draft",
    timeout_s=30,
)

STUB_OUTPUT = ScheduleTankerAarOutput(
    tanker_routes=[
        TankerRoute(
            tail="IL-7801",
            sequence=[AarStop(aarp="AARP-1", receiver="SU-A0123", time=STUB_TIME, offload_kg=4000.0)],
        )
    ],
    unserved=[RefuelEvent(mission_id="M-1002", tail_no="SU-A0456", fuel_demand_kg=2000.0, reason="skeleton")],
    spare_fuel_kg={"IL-7801": 8000.0},
    kpis={"served": 1.0, "unserved": 1.0},
)


async def schedule_tanker_aar(payload: ScheduleTankerAarInput, ctx: ToolContext) -> ScheduleTankerAarOutput:
    problem = build_problem(payload, ctx)
    solution = solve(problem)
    explanation = explain(problem, solution)

    routes = [
        TankerRoute(
            tail=tanker.tail,
            sequence=[
                AarStop(
                    aarp=problem.aarps[stop.aarp_idx].aarp_id,
                    receiver=problem.demands[stop.demand_idx].tail_no,
                    time=problem.origin + timedelta(minutes=stop.time_min),
                    offload_kg=stop.offload_kg,
                )
                for stop in route
            ],
        )
        for tanker, route in zip(problem.tankers, solution.routes)
        if route
    ]
    unserved = [
        RefuelEvent(
            mission_id=problem.demands[i].mission_id,
            tail_no=problem.demands[i].tail_no,
            fuel_demand_kg=problem.demands[i].fuel_kg,
            reason=reason,
        )
        for i, reason in solution.unserved
    ] + [
        RefuelEvent(mission_id=m, tail_no=t, fuel_demand_kg=f, reason=r)
        for m, t, f, r in problem.pre_unserved
    ]
    served = sum(len(route) for route in solution.routes)
    return ScheduleTankerAarOutput(
        tanker_routes=routes,
        unserved=unserved,
        spare_fuel_kg=explanation.spare_fuel_kg,
        kpis={
            "served": float(served),
            "unserved": float(len(unserved)),
            "offload_kg": float(sum(s.offload_kg for r in solution.routes for s in r)),
            "tankers_used": float(len(routes)),
            "travel_min": solution.travel_min,
        },
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_tanker_aar)
