"""Return value of the retrieval sub-agent. Tool outputs, plus a question when context is missing."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class RetrievalResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    query: str
    calls: list[ToolCallRecord] = Field(default_factory=list)
    question: str | None = None
    session: Any | None = Field(default=None, exclude=True)
