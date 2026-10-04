"""Return value of a task agent. Tool outputs, plus the finish note sent back to the main agent."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from garuda.harness.retrieval.schemas import ToolCallRecord

TaskStatus = Literal["done", "partial", "blocked"]


class TaskFinishInput(BaseModel):
    """Hand this task back to the main agent. Do not restate numbers; those stay in the tool outputs."""

    status: TaskStatus = Field(description="done, partial, or blocked.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Defaults and unstated choices you used. No invented ids, and no counts.",
    )
    note: str = Field(
        default="",
        description="One short reason for the status. No numbers, counts, coordinates, or probabilities.",
    )


class TaskResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str
    query: str
    main_calls: list[ToolCallRecord] = Field(default_factory=list)
    agent_calls: list[ToolCallRecord] = Field(default_factory=list)
    status: TaskStatus | None = None
    assumptions: list[str] = Field(default_factory=list)
    note: str = ""
    question: str | None = None
    session: Any | None = Field(default=None, exclude=True)
