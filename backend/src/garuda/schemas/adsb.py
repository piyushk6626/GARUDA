from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdsbTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    source_id: str = Field(min_length=1)
    icao24: str = Field(pattern=r"^[0-9a-f]{6}$")
    callsign: str | None = Field(default=None, max_length=8)
    origin_country: str = Field(min_length=1)
    last_contact: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None
    geo_alt_m: float | None = None
    speed_ms: float | None = Field(default=None, ge=0)
    heading: float | None = Field(default=None, ge=0, lt=360)
    vrate: float | None = None
    on_ground: bool
    position_source: int = Field(ge=0, le=3)

    @model_validator(mode="after")
    def check_times_and_finite(self) -> "AdsbTrack":
        if self.time.tzinfo is None or self.last_contact.tzinfo is None:
            raise ValueError("track times must be timezone-aware")
        for name in ("lat", "lon", "alt_m", "geo_alt_m", "speed_ms", "heading", "vrate"):
            value = getattr(self, name)
            if value is None:
                continue
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError(f"{name} must be finite")
        return self
