"""Route and altitude checks over generated airspace polygons."""

from datetime import datetime

from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry

from garuda.schemas.airspace import AirspaceZone, is_active


def zones_intersecting(
    zones: list[AirspaceZone],
    route_wkt: str | None,
    altitude_ft: int | None,
    at: datetime,
) -> list[AirspaceZone]:
    """Intersect a route or polygon; None omits spatial or altitude filtering."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    route = shapely_wkt.loads(route_wkt) if route_wkt is not None else None
    if route is not None and route.is_empty:
        raise ValueError("route geometry is empty")
    matched = [
        zone
        for zone in zones
        if is_active(zone, at)
        and (altitude_ft is None or zone.floor_ft <= altitude_ft <= zone.ceiling_ft)
        and (route is None or _intersects(zone, route))
    ]
    matched.sort(key=lambda zone: zone.zone_id)
    return matched


def check_airspace(
    zones: list[AirspaceZone],
    route_wkt: str,
    altitude_ft: int,
    at: datetime,
) -> list[dict[str, object]]:
    """Findings for the harness. Active is not the same thing as prohibited."""
    findings: list[dict[str, object]] = []
    for zone in zones_intersecting(zones, route_wkt, altitude_ft, at):
        findings.append(
            {
                "zone_id": zone.zone_id,
                "type": zone.type,
                "controlling_authority": zone.controlling_authority,
                "capacity_per_hour": zone.capacity_per_hour,
                "mission_id": zone.mission_id,
                "source": zone.source,
            }
        )
    return findings


def _intersects(zone: AirspaceZone, route: BaseGeometry) -> bool:
    footprint = shapely_wkt.loads(zone.wkt)
    return footprint.intersects(route)
