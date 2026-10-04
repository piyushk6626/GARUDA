from garuda.contracts.tool import ToolContext


def build_context(scenario=None, *, use_tool_backed: bool = False) -> ToolContext:
    """Build a ToolContext from a scenario (memory) or settings (postgres). TODO(phase1)."""
    raise NotImplementedError
