from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.compute_sortie_capacity.schemas import ComputeSortieCapacityInput, ComputeSortieCapacityOutput

SPEC = ToolSpec(
    name="compute_sortie_capacity",
    description="Sorties per base per hour and the bottleneck; use for capacity and surge what-ifs.",
    input_model=ComputeSortieCapacityInput,
    output_model=ComputeSortieCapacityOutput,
    side_effects="none",
    timeout_s=10,
)


async def compute_sortie_capacity(payload: ComputeSortieCapacityInput, ctx: ToolContext) -> ComputeSortieCapacityOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, compute_sortie_capacity)
