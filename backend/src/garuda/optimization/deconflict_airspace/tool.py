from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.deconflict_airspace.schemas import DeconflictAirspaceInput, DeconflictAirspaceOutput

SPEC = ToolSpec(
    name="deconflict_airspace",
    description="Shift times/altitudes so flights respect separation and zone capacity.",
    input_model=DeconflictAirspaceInput,
    output_model=DeconflictAirspaceOutput,
    side_effects="draft",
    timeout_s=30,
)


async def deconflict_airspace(payload: DeconflictAirspaceInput, ctx: ToolContext) -> DeconflictAirspaceOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, deconflict_airspace)
