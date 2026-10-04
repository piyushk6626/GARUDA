"""Data tool `get_airspace`. Backing: queries/airspace.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime
from math import isfinite

from pydantic import BaseModel
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.queries.airspace import zones_intersecting


class AirspaceItem(BaseModel):
    zone_id: str
    name: str
    type: str
    floor_ft: int
    ceiling_ft: int


class GetAirspaceInput(BaseModel):
    route: list[tuple[float, float]] | None = None
    polygon: list[tuple[float, float]] | None = None
    altitude_ft: int | None = None
    time: datetime | None = None


class GetAirspaceOutput(BaseModel):
    total: int
    items: list[AirspaceItem]


SPEC = ToolSpec(
    name="get_airspace",
    description="Zones active along a route, time, and altitude. Use before routing; not for deconfliction.",
    input_model=GetAirspaceInput,
    output_model=GetAirspaceOutput,
    side_effects="none",
    timeout_s=10,
)

# Retained for the legacy data-tool skeleton graph's import.
STUB_OUTPUT = GetAirspaceOutput(
    total=1,
    items=[AirspaceItem(zone_id="CTR-B1", name="Base 1 CTR", type="CTR", floor_ft=0, ceiling_ft=10000)],
)


async def get_airspace(payload: GetAirspaceInput, ctx: ToolContext) -> GetAirspaceOutput:
    """Read active scenario zones, optionally filtering by geometry and altitude.

    Route and polygon are mutually exclusive. Omitted time uses scenario_time;
    omitted geometry or altitude leaves that dimension unfiltered.
    """
    geometry = _query_geometry(payload)
    at = payload.time if payload.time is not None else ctx.scenario_time
    if at.tzinfo is None or at.utcoffset() is None:
        raise ToolInputError("time must be timezone-aware (include a UTC offset)")
    zones = zones_intersecting(
        ctx.scenario.snapshot().zones,
        geometry.wkt if geometry is not None else None,
        payload.altitude_ft,
        at,
    )
    items = [
        AirspaceItem(
            zone_id=zone.zone_id,
            name=zone.name,
            type=zone.type,
            floor_ft=zone.floor_ft,
            ceiling_ft=zone.ceiling_ft,
        )
        for zone in zones
    ]
    return GetAirspaceOutput(total=len(items), items=items)


def _query_geometry(payload: GetAirspaceInput) -> BaseGeometry | None:
    if payload.route is not None and payload.polygon is not None:
        raise ToolInputError("provide route or polygon, not both")
    if payload.route is not None:
        name, points, minimum = "route", payload.route, 2
    elif payload.polygon is not None:
        name, points, minimum = "polygon", payload.polygon, 3
    else:
        return None
    if len(points) < minimum:
        raise ToolInputError(f"{name} needs at least {minimum} latitude/longitude points")
    for lat, lon in points:
        if not isfinite(lat) or not isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ToolInputError(f"{name} points must be finite latitude/longitude coordinates within bounds")
    coordinates = [(lon, lat) for lat, lon in points]
    geometry = LineString(coordinates) if name == "route" else Polygon(coordinates)
    if geometry.is_empty or not geometry.is_valid:
        raise ToolInputError(f"{name} geometry must be non-empty and valid")
    return geometry
