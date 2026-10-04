import asyncio

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.compute_sortie_capacity.explain import explain
from garuda.optimization.compute_sortie_capacity.model import solve
from garuda.optimization.compute_sortie_capacity.problem import build_problem
from garuda.optimization.compute_sortie_capacity.schemas import (
    ComputeSortieCapacityInput,
    ComputeSortieCapacityOutput,
)

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
    """Compute read-only capacity from the snapshot and frozen provider interfaces."""
    # The shared CPU executor is still a stub; keep the solve off the event loop.
    return await asyncio.to_thread(_compute, payload, ctx)


def _compute(payload, ctx):
    problem = build_problem(payload, ctx)
    return explain(problem, solve(problem))


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, compute_sortie_capacity)
