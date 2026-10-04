from garuda.contracts.tool import ToolContext, ToolSpec
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
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_tanker_aar)
