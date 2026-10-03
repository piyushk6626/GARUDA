"""Density and observed-only conflict candidates for downstream optimizers."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ..schemas.adsb import PositionObservation


class ConflictAssessment(BaseModel):
    icao24: str
    assessment: Literal["candidate", "clear", "unknown"]
    reason: str


def conflict_candidates(observations: list[PositionObservation], *, lon: float, lat: float,
                        altitude_m: float | None, altitude_reference: str | None,
                        observed_at: datetime) -> list[ConflictAssessment]:
    """Observed points only; it intentionally makes no future trajectory prediction."""
    result = []
    for item in observations:
        if abs((item.time-observed_at).total_seconds()) > 300: continue
        lateral_m = (((item.lat-lat)**2 + ((item.lon-lon)*0.9)**2) ** .5) * 111_000
        if lateral_m > 10_000: continue
        if altitude_m is None or item.alt_m is None or altitude_reference not in {"barometric", "geometric"}:
            result.append(ConflictAssessment(icao24=item.icao24, assessment="unknown", reason="unknown or incompatible altitude reference"))
        elif abs(item.alt_m-altitude_m) <= 304.8:
            result.append(ConflictAssessment(icao24=item.icao24, assessment="candidate", reason="observed lateral/vertical threshold"))
        else:
            result.append(ConflictAssessment(icao24=item.icao24, assessment="clear", reason="vertical separation"))
    return result
