"""Data tool `get_civil_traffic`. Backing: queries/adsb.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime
from math import isfinite

from pydantic import BaseModel
from shapely.geometry import Point, Polygon

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.queries.adsb import latest_tracks


class TrafficItem(BaseModel):
    icao24: str
    callsign: str | None = None
    lat: float
    lon: float
    alt_m: float | None = None


class GetCivilTrafficInput(BaseModel):
    polygon: list[tuple[float, float]]
    since: datetime


class GetCivilTrafficOutput(BaseModel):
    total: int
    items: list[TrafficItem]


SPEC = ToolSpec(
    name="get_civil_traffic",
    description="ADS-B tracks in an area since a time. Use for deconfliction context; not for threat tracks.",
    input_model=GetCivilTrafficInput,
    output_model=GetCivilTrafficOutput,
    side_effects="none",
    timeout_s=10,
)

# Compatibility fixture for the separate data graph scaffold.
# The callable tool below reads stored observations instead.
STUB_OUTPUT = GetCivilTrafficOutput(
    total=1,
    items=[TrafficItem(icao24="abc123", callsign="AIC101", lat=28.5, lon=77.1, alt_m=10000.0)],
)


async def get_civil_traffic(payload: GetCivilTrafficInput, ctx: ToolContext) -> GetCivilTrafficOutput:
    """Latest stored positions in a (lat, lon) polygon, including its boundary.

    Position times must fall in [since, scenario_time]. This historical query
    does not impose the live helper's 120-second freshness window. Latest-track
    selection uses the repository's (source_id, icao24) identity and tie behavior
    within that interval, before testing the position against the polygon.

    Counts describe matching stored observations, not current airspace coverage
    or clearance. Missing observations, including an empty result, cannot prove
    an area is empty; returned historical positions may be stale for routing.
    """
    if payload.since.tzinfo is None or payload.since.utcoffset() is None:
        raise ToolInputError("since must be timezone-aware")
    if ctx.scenario_time.tzinfo is None or ctx.scenario_time.utcoffset() is None:
        raise ToolInputError("scenario_time must be timezone-aware")
    if payload.since > ctx.scenario_time:
        raise ToolInputError("since must not be after scenario_time")
    if len(payload.polygon) < 3:
        raise ToolInputError("polygon needs at least three distinct (lat, lon) vertices")
    for lat, lon in payload.polygon:
        if not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ToolInputError("polygon vertices must be finite (lat, lon) coordinates in range")
    footprint = Polygon([(lon, lat) for lat, lon in payload.polygon])
    if footprint.is_empty or not footprint.is_valid or footprint.area == 0:
        raise ToolInputError("polygon must be a valid, non-zero-area ring")

    observations = [
        track
        for track in ctx.scenario.snapshot().adsb_tracks
        if payload.since <= track.time <= ctx.scenario_time
    ]
    matched = [
        track
        for track in latest_tracks(observations)
        if footprint.covers(Point(track.lon, track.lat))
    ]
    matched.sort(key=lambda track: (track.icao24, track.source_id))
    items = [
        TrafficItem(
            icao24=track.icao24,
            callsign=track.callsign,
            lat=track.lat,
            lon=track.lon,
            alt_m=track.alt_m,
        )
        for track in matched
    ]
    return GetCivilTrafficOutput(total=len(items), items=items)
