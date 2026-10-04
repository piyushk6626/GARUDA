from collections.abc import Awaitable, Callable

from garuda.contracts.tool import ToolSpec

ToolFn = Callable[..., Awaitable[object]]


def register(spec: ToolSpec, fn: ToolFn) -> None:
    raise NotImplementedError


def get_tool(name: str) -> tuple[ToolSpec, ToolFn]:
    raise NotImplementedError


def list_tools(side_effects: str | None = None) -> list[ToolSpec]:
    raise NotImplementedError
