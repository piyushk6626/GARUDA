from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.compute_sortie_capacity.schemas import (
    ComputeSortieCapacityInput,
    ComputeSortieCapacityOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="compute_sortie_capacity",
    description="Sorties per base per hour and the bottleneck; use for capacity and surge what-ifs.",
    input_model=ComputeSortieCapacityInput,
    output_model=ComputeSortieCapacityOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = ComputeSortieCapacityOutput(
    capacity={"B1": [2, 2, 2, 2]},
    totals={"B1": 8},
    bottlenecks={"B1": "ground_crew"},
    shadow_prices={"B1": {"ground_crew": 1.0, "aircraft": 0.5}},
)


async def compute_sortie_capacity(
    payload: ComputeSortieCapacityInput, ctx: ToolContext
) -> ComputeSortieCapacityOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, compute_sortie_capacity)
