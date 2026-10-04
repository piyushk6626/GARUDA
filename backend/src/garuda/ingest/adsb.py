"""Normalize an OpenSky state-vector response into track rows.

Malformed rows are counted and skipped. A missing position time is never
replaced with the poll time, and a null altitude stays null.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from garuda.schemas.adsb import AdsbTrack


@dataclass(frozen=True)
class NormalizeResult:
    observations: tuple[AdsbTrack, ...]
    dropped: int


def normalize_opensky_response(
    document: dict,
    *,
    source_id: str,
    ingested_at: datetime | None = None,
) -> NormalizeResult:
    del ingested_at
    if not source_id:
        raise ValueError("source_id is required")
    states = document.get("states")
    if states is None:
        return NormalizeResult((), 0)
    if not isinstance(states, list):
        raise ValueError("OpenSky states must be a list or null")
    kept: list[AdsbTrack] = []
    dropped = 0
    for row in states:
        if not isinstance(row, list):
            dropped += 1
            continue
        track = _observation(row, source_id)
        if track is None:
            dropped += 1
            continue
        kept.append(track)
    return NormalizeResult(tuple(kept), dropped)


def _observation(row: list, source_id: str) -> AdsbTrack | None:
    icao = _text(_at(row, 0))
    if icao is None or len(icao) != 6 or any(char not in "0123456789abcdef" for char in icao):
        return None
    position_time = _epoch(_at(row, 3))
    longitude = _float(_at(row, 5))
    latitude = _float(_at(row, 6))
    if position_time is None or longitude is None or latitude is None:
        return None
    if not _in_range(latitude, -90, 90) or not _in_range(longitude, -180, 180):
        return None
    last_contact = _epoch(_at(row, 4)) or position_time
    speed = _optional_float(_at(row, 9))
    heading = _optional_float(_at(row, 10))
    vertical_rate = _optional_float(_at(row, 11))
    if speed is not None and speed < 0:
        return None
    if heading is not None and not _in_range(heading, 0, 360, upper_exclusive=True):
        return None
    callsign = _callsign(_at(row, 1))
    origin = _label(_at(row, 2)) or "UNKNOWN"
    try:
        return AdsbTrack(
            time=position_time,
            source_id=source_id,
            icao24=icao,
            callsign=callsign,
            origin_country=origin,
            last_contact=last_contact,
            lat=latitude,
            lon=longitude,
            alt_m=_optional_float(_at(row, 7)),
            geo_alt_m=_optional_float(_at(row, 13)),
            speed_ms=speed,
            heading=heading,
            vrate=vertical_rate,
            on_ground=bool(_at(row, 8)) if len(row) > 8 and _at(row, 8) is not None else False,
            position_source=_position_source(_at(row, 16)),
        )
    except ValueError:
        return None


def _at(row: list, index: int) -> object:
    if len(row) <= index:
        return None
    return row[index]


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def _label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _callsign(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    if not cleaned:
        return None
    return cleaned[:8]


def _epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in {float("inf"), float("-inf")}:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _float(value)


def _position_source(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    source = int(value)
    if source < 0 or source > 3:
        return 0
    return source


def _in_range(value: float, low: float, high: float, *, upper_exclusive: bool = False) -> bool:
    if value < low:
        return False
    if upper_exclusive:
        return value < high
    return value <= high
