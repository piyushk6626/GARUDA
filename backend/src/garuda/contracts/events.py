from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal["aircraft_nmc", "new_sam", "base_closed", "p1_request", "tanker_lost", "weather_override"]


class Event(BaseModel):
    type: EventType
    entity_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    time: datetime
