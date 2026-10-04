from garuda.contracts.results import ToolResult
from garuda.contracts.tool import ToolContext


async def run_tool(name: str, raw_args: dict, ctx: ToolContext) -> ToolResult:
    """Validate args, enforce timeout, catch ToolInputError, set data_as_of. TODO(phase1)."""
    raise NotImplementedError
