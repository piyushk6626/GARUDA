"""Synthetic theatre airspace in a local projected CRS.

Boxes, racetracks, and buffers are built in metres and then stored as
EPSG:4326 polygons. Longitude and latitude are never treated as kilometres.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import to_wkt
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from garuda.common import as_utc as _as_utc
from garuda.config import ScenarioConfig
from garuda.generators.gis import BORDER_GEOJSON
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.mission import Mission

METRIC_CRS = "EPSG:32643"
ADIZ_BUFFER_KM = 25.0
CAP_COUNT = 8
AAR_COUNT = 3
CORRIDOR_COUNT = 4
PLACEMENT_STREAM = 41
_TO_METRIC = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
_TO_GEOGRAPHIC = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)


def build_airspace(
    config: ScenarioConfig,
    seed: int,
    missions: list[Mission] | None = None,
    border_path: Path | None = None,
) -> list[AirspaceZone]:
    start = _as_utc(config.start)
    theatre = _project(_theatre_geographic(config))
    border = _project(_load_border(border_path or BORDER_GEOJSON))
    if not isinstance(border, LineString):
        raise ValueError("airspace border fixture must be a LineString")
    rng = np.random.default_rng(np.random.SeedSequence([seed, PLACEMENT_STREAM]))
    mission_id = _linked_mission(missions or [])
    zones: list[AirspaceZone] = []
    zones.extend(_caps(border, theatre))
    zones.extend(_tracks(rng, theatre, kind="AAR_TRACK", count=AAR_COUNT, leg_m=200_000))
    zones.extend(_tracks(rng, theatre, kind="CORRIDOR", count=CORRIDOR_COUNT, leg_m=120_000))
    zones.extend(_tras(theatre, start, mission_id))
    zones.append(_adiz(border, theatre))
    zones.extend(_missile_zones(theatre))
    zones.sort(key=lambda zone: zone.zone_id)
    return zones


def _caps(border: LineString, theatre: Polygon) -> list[AirspaceZone]:
    zones: list[AirspaceZone] = []
    fractions = [0.12 + index * 0.09 for index in range(CAP_COUNT)]
    for index, fraction in enumerate(fractions, start=1):
        footprint = _cap_box(border, theatre, fraction)
        area_km2 = footprint.area / 1_000_000.0
        if abs(area_km2 - 3200.0) > 1.0:
            raise ValueError(f"CAP box area is {area_km2:.1f} km^2, expected 3200")
        zones.append(
            _zone(
                f"Z-CAP-{index:02d}",
                f"CAP-{index:02d}",
                "CAP",
                footprint,
                floor_ft=15_000,
                ceiling_ft=35_000,
                capacity=2,
            )
        )
    return zones


def _cap_box(border: LineString, theatre: Polygon, fraction: float) -> Polygon:
    for shift in (0.0, -0.05, 0.05, -0.1, 0.1):
        point = border.interpolate(min(0.92, max(0.08, fraction + shift)), normalized=True)
        for east_km in (70, 90, 50, 110):
            center = Point(point.x + east_km * 1000.0, point.y)
            rect = box(center.x - 40_000.0, center.y - 20_000.0, center.x + 40_000.0, center.y + 20_000.0)
            if isinstance(rect, Polygon) and theatre.covers(rect):
                return rect
    raise ValueError(f"CAP box at fraction {fraction:.2f} does not fit in the theatre")


def _tracks(
    rng: np.random.Generator,
    theatre: Polygon,
    *,
    kind: str,
    count: int,
    leg_m: float,
) -> list[AirspaceZone]:
    prefix = "Z-AAR" if kind == "AAR_TRACK" else "Z-COR"
    label = "AAR" if kind == "AAR_TRACK" else "CORRIDOR"
    floor = 20_000 if kind == "AAR_TRACK" else 5_000
    ceiling = 28_000 if kind == "AAR_TRACK" else 40_000
    capacity = 1 if kind == "AAR_TRACK" else 4
    minx, miny, maxx, maxy = theatre.bounds
    margin = leg_m / 2.0 + 15_000.0
    zones: list[AirspaceZone] = []
    for index in range(1, count + 1):
        centerline, footprint = _fit_track(rng, theatre, minx, miny, maxx, maxy, margin, leg_m)
        _assert_width(centerline, footprint, 10_000.0)
        zones.append(
            _zone(
                f"{prefix}-{index:02d}",
                f"{label}-{index:02d}",
                kind,
                footprint,
                floor_ft=floor,
                ceiling_ft=ceiling,
                capacity=capacity,
                width_km=20.0,
            )
        )
    return zones


def _fit_track(
    rng: np.random.Generator,
    theatre: Polygon,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    margin: float,
    leg_m: float,
) -> tuple[LineString, Polygon]:
    if maxx - minx <= 2 * margin or maxy - miny <= 30_000:
        raise ValueError("theatre is too small for a buffered track")
    for _attempt in range(40):
        x = float(rng.uniform(minx + margin, maxx - margin))
        y = float(rng.uniform(miny + 20_000.0, maxy - 20_000.0))
        angle = float(rng.uniform(-0.4, 0.4))
        dx = math.cos(angle) * leg_m / 2.0
        dy = math.sin(angle) * leg_m / 2.0
        centerline = LineString([(x - dx, y - dy), (x + dx, y + dy)])
        footprint = centerline.buffer(10_000.0)
        if theatre.covers(footprint) and isinstance(footprint, Polygon):
            return centerline, footprint
    raise ValueError("could not place a track inside the theatre")


def _tras(theatre: Polygon, start: datetime, mission_id: str | None) -> list[AirspaceZone]:
    minx, miny, maxx, maxy = theatre.bounds
    windows = (
        (start + timedelta(hours=6), start + timedelta(hours=9)),
        (start + timedelta(hours=30), start + timedelta(hours=33)),
    )
    zones: list[AirspaceZone] = []
    for index, (active_from, active_to) in enumerate(windows, start=1):
        x = minx + index * (maxx - minx) / 3.0
        y = (miny + maxy) / 2.0
        footprint = box(x - 25_000.0, y - 25_000.0, x + 25_000.0, y + 25_000.0)
        if not theatre.covers(footprint):
            raise ValueError("TRA box falls outside the theatre")
        zones.append(
            _zone(
                f"Z-TRA-{index:02d}",
                f"TRA-{index:02d}",
                "TRA",
                footprint,
                floor_ft=10_000,
                ceiling_ft=40_000,
                capacity=4,
                active_from=active_from,
                active_to=active_to,
                mission_id=mission_id,
            )
        )
    return zones


def _adiz(border: LineString, theatre: Polygon) -> AirspaceZone:
    buffered = border.buffer(ADIZ_BUFFER_KM * 1000.0).intersection(theatre)
    footprint = _largest_polygon(buffered)
    if footprint.is_empty:
        raise ValueError("ADIZ buffer is empty")
    return _zone(
        "Z-ADIZ",
        "ADIZ-SYNTHETIC",
        "ADIZ",
        footprint,
        floor_ft=0,
        ceiling_ft=60_000,
        capacity=None,
    )


def _missile_zones(theatre: Polygon) -> list[AirspaceZone]:
    minx, miny, maxx, maxy = theatre.bounds
    zones: list[AirspaceZone] = []
    for index in range(1, 3):
        x = minx + index * (maxx - minx) / 3.0
        y = miny + index * (maxy - miny) / 4.0
        footprint = box(x - 30_000.0, y - 30_000.0, x + 30_000.0, y + 30_000.0)
        if not theatre.covers(footprint):
            raise ValueError("missile-engagement zone falls outside the theatre")
        zones.append(
            _zone(
                f"Z-MEZ-{index:02d}",
                f"MEZ-{index:02d}",
                "RESTRICTED",
                footprint,
                floor_ft=0,
                ceiling_ft=50_000,
                capacity=0,
            )
        )
    return zones


def _zone(
    zone_id: str,
    name: str,
    kind: str,
    footprint: Polygon,
    *,
    floor_ft: int,
    ceiling_ft: int,
    capacity: int | None,
    width_km: float | None = None,
    active_from: datetime | None = None,
    active_to: datetime | None = None,
    mission_id: str | None = None,
) -> AirspaceZone:
    geographic = transform(_TO_GEOGRAPHIC.transform, footprint)
    if not isinstance(geographic, Polygon) or geographic.is_empty or not geographic.is_valid:
        raise ValueError(f"{zone_id} did not transform to a valid polygon")
    return AirspaceZone(
        zone_id=zone_id,
        name=name,
        type=kind,
        wkt=to_wkt(geographic, rounding_precision=6),
        floor_ft=floor_ft,
        ceiling_ft=ceiling_ft,
        active_from=active_from,
        active_to=active_to,
        controlling_authority="IAF",
        source="synthetic",
        capacity_per_hour=capacity,
        width_km=width_km,
        mission_id=mission_id,
    )


def _assert_width(centerline: LineString, footprint: Polygon, radius_m: float) -> None:
    start, end = centerline.coords[0], centerline.coords[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if abs(length - centerline.length) > 1.0:
        raise ValueError("track centerline length is inconsistent")
    midpoint = centerline.interpolate(0.5, normalized=True)
    px, py = -dy / length, dx / length
    inside = Point(midpoint.x + px * (radius_m - 500.0), midpoint.y + py * (radius_m - 500.0))
    outside = Point(midpoint.x + px * (radius_m + 1_500.0), midpoint.y + py * (radius_m + 1_500.0))
    if not footprint.covers(inside) or footprint.covers(outside):
        raise ValueError("track width is not 20 km")


def _largest_polygon(geometry: BaseGeometry) -> Polygon:
    if isinstance(geometry, Polygon):
        return geometry
    polygons = [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]
    if not polygons:
        raise ValueError("buffered border produced no polygon")
    return max(polygons, key=lambda part: part.area)


def _linked_mission(missions: list[Mission]) -> str | None:
    for mission in missions:
        if mission.type == "CAP":
            return mission.mission_id
    if missions:
        return missions[0].mission_id
    return None


def _theatre_geographic(config: ScenarioConfig) -> Polygon:
    box_geom = box(
        config.bbox.lon_min,
        config.bbox.lat_min,
        config.bbox.lon_max,
        config.bbox.lat_max,
    )
    if not isinstance(box_geom, Polygon):
        raise ValueError("theatre bounding box is not a polygon")
    return box_geom


def _load_border(path: Path) -> LineString:
    raw = json.loads(path.read_text())
    coordinates = raw["features"][0]["geometry"]["coordinates"]
    line = LineString(coordinates)
    if line.is_empty:
        raise ValueError(f"border geometry is empty: {path}")
    return line


def _project(geometry: BaseGeometry) -> BaseGeometry:
    return transform(_TO_METRIC.transform, geometry)
