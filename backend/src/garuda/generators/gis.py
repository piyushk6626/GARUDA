"""Static geography for the western theatre.

Runway counts and lengths come from the OurAirports fixture. Shelter, fuel,
crew, and sortie capacity are seeded. The elevation grid is a synthetic field
on a 1 km mesh; it is not stored as PostGIS rows.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyproj import Geod
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString, Point, box, shape
from shapely.geometry.base import BaseGeometry

from garuda.bases import BASES
from garuda.config import BoundingBox, ScenarioConfig
from garuda.schemas.gis import (
    BaseRecord,
    BaseTargetDistance,
    BorderFeature,
    GeoFeature,
    TerrainMeta,
)
from garuda.schemas.mission import Target

CELL_KM = 1.0
ROAD_LIMIT_KM = 300.0
ROAD_LINKS = 2
_FEET_TO_METRES = 0.3048
_GEOD = Geod(ellps="WGS84")
FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "gis"
AIRPORTS_CSV = FIXTURE_DIR / "ourairports_airports.csv"
RUNWAYS_CSV = FIXTURE_DIR / "ourairports_runways.csv"
BORDER_GEOJSON = FIXTURE_DIR / "border.geojson"
PLACES_GEOJSON = FIXTURE_DIR / "places.geojson"
_FEATURE_KINDS = {"road", "city", "river", "water"}
_BORDER_KINDS = {"international", "coastline", "admin"}


@dataclass(frozen=True)
class TerrainGrid:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    cell_km: float
    rows: int
    cols: int
    elev_m: np.ndarray
    slope_deg: np.ndarray
    terrain_mask: np.ndarray
    terrain_penalty: np.ndarray


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _forward, _back, metres = _GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(metres) / 1000.0


def feet_to_metres(length_ft: float) -> int:
    return int(round(length_ft * _FEET_TO_METRES))


def map_runways(airports_csv: Path, runways_csv: Path) -> dict[str, tuple[int, int]]:
    idents = _airport_idents(airports_csv)
    lengths: dict[str, list[int]] = {ident: [] for ident in idents}
    with runways_csv.open(newline="") as handle:
        for row in csv.DictReader(handle):
            ident = row["airport_ident"]
            if ident not in lengths or row["closed"] == "1" or not row["length_ft"]:
                continue
            lengths[ident].append(feet_to_metres(float(row["length_ft"])))
    missing = [ident for ident, rows in lengths.items() if not rows]
    if missing:
        raise ValueError(f"no open runway for {', '.join(sorted(missing))}")
    return {ident: (len(rows), max(rows)) for ident, rows in lengths.items()}


def map_geojson(
    payload: object,
    bbox: BoundingBox,
) -> tuple[list[GeoFeature], list[BorderFeature]]:
    if not isinstance(payload, dict):
        raise ValueError("geojson payload must be an object")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("geojson payload needs a features list")
    theatre = box(bbox.lon_min, bbox.lat_min, bbox.lon_max, bbox.lat_max)
    places: list[GeoFeature] = []
    borders: list[BorderFeature] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        clipped = shape(geometry).intersection(theatre)
        kind = str(props.get("kind") or "")
        if kind in _BORDER_KINDS:
            lines = _as_multiline(clipped)
            if lines is None:
                continue
            borders.append(
                BorderFeature(
                    border_id=str(props["border_id"]),
                    name=str(props["name"]),
                    kind=kind,  # type: ignore[arg-type]
                    wkt=lines.wkt,
                )
            )
            continue
        if kind not in _FEATURE_KINDS or clipped.is_empty:
            continue
        places.append(
            GeoFeature(
                feature_id=str(props["feature_id"]),
                kind=kind,  # type: ignore[arg-type]
                name=str(props["name"]),
                wkt=clipped.wkt,
            )
        )
    return places, borders


def build_gis(
    config: ScenarioConfig,
    seed: int,
    targets: list[Target],
) -> tuple[list[BaseRecord], list[GeoFeature], list[BorderFeature], list[BaseTargetDistance], TerrainMeta]:
    bases = build_bases(config, seed)
    features, borders = build_features(config.bbox)
    distances = build_distances(bases, targets)
    terrain = terrain_meta(build_terrain(config.bbox, seed))
    return bases, features, borders, distances, terrain


def build_bases(
    config: ScenarioConfig,
    seed: int,
    runways: dict[str, tuple[int, int]] | None = None,
) -> list[BaseRecord]:
    if config.bases < 1:
        return []
    if config.bases > len(BASES):
        raise ValueError(f"scenario asks for {config.bases} bases; catalogue has {len(BASES)}")
    stats = runways if runways is not None else map_runways(AIRPORTS_CSV, RUNWAYS_CSV)
    rng = np.random.default_rng(seed)
    records: list[BaseRecord] = []
    for airfield in BASES[: config.bases]:
        if airfield.icao not in stats:
            raise ValueError(f"no runway row for {airfield.icao}")
        count, length_m = stats[airfield.icao]
        shelter, fuel, crews, sorties = _capacity(rng, length_m)
        records.append(
            BaseRecord(
                base_id=airfield.base_id,
                name=airfield.name,
                icao=airfield.icao,
                lat=airfield.lat,
                lon=airfield.lon,
                elevation_ft=airfield.elevation_ft,
                runway_count=count,
                runway_length_m=length_m,
                shelter_count=shelter,
                fuel_capacity_l=fuel,
                ground_crew_teams=crews,
                max_sorties_per_hour=sorties,
            )
        )
    return records


def build_features(bbox: BoundingBox) -> tuple[list[GeoFeature], list[BorderFeature]]:
    places, _ignored = map_geojson(json.loads(PLACES_GEOJSON.read_text()), bbox)
    _places, borders = map_geojson(json.loads(BORDER_GEOJSON.read_text()), bbox)
    roads = _roads(places)
    features = sorted([*places, *roads], key=lambda feature: feature.feature_id)
    return features, sorted(borders, key=lambda border: border.border_id)


def build_distances(bases: list[BaseRecord], targets: list[Target]) -> list[BaseTargetDistance]:
    rows: list[BaseTargetDistance] = []
    for base in bases:
        for target in targets:
            rows.append(
                BaseTargetDistance(
                    base_id=base.base_id,
                    target_id=target.target_id,
                    distance_km=round(great_circle_km(base.lat, base.lon, target.lat, target.lon), 3),
                )
            )
    return rows


def build_terrain(bbox: BoundingBox, seed: int) -> TerrainGrid:
    rows, cols = grid_shape(bbox)
    lat = bbox.lat_min + (np.arange(rows) + 0.5) * ((bbox.lat_max - bbox.lat_min) / rows)
    lon = bbox.lon_min + (np.arange(cols) + 0.5) * ((bbox.lon_max - bbox.lon_min) / cols)
    lat_grid = lat[:, None]
    lon_grid = lon[None, :]
    ridge = np.exp(-((lon_grid - 74.2) ** 2) / 2.2)
    north = np.clip((lat_grid - 24.0) / 9.0, 0.0, 1.0)
    west_low = np.clip((70.5 - lon_grid) / 4.0, 0.0, 1.0)
    field = 90.0 + 28.0 * (lat_grid - 22.0) + 480.0 * ridge * (0.45 + 0.55 * north) - 220.0 * west_low
    noise = np.random.default_rng(seed).normal(0.0, 6.0, size=field.shape)
    elev = np.clip(field + noise, 5.0, 1600.0)
    return layers_from_elevation(bbox, elev)


def layers_from_elevation(bbox: BoundingBox, elev_m: np.ndarray) -> TerrainGrid:
    rows, cols = elev_m.shape
    expected_rows, expected_cols = grid_shape(bbox)
    if (rows, cols) != (expected_rows, expected_cols):
        raise ValueError(f"elevation grid is {rows}x{cols}; bbox expects {expected_rows}x{expected_cols}")
    slope = _slope_deg(bbox, elev_m)
    span = float(elev_m.max() - elev_m.min())
    mask = np.zeros_like(elev_m) if span == 0 else (elev_m - float(elev_m.min())) / span
    slope_span = float(slope.max())
    slope_norm = np.zeros_like(slope) if slope_span == 0 else slope / slope_span
    penalty = np.clip(0.65 * mask + 0.35 * slope_norm, 0.0, 1.0)
    return TerrainGrid(
        lat_min=bbox.lat_min,
        lat_max=bbox.lat_max,
        lon_min=bbox.lon_min,
        lon_max=bbox.lon_max,
        cell_km=CELL_KM,
        rows=rows,
        cols=cols,
        elev_m=elev_m,
        slope_deg=slope,
        terrain_mask=mask,
        terrain_penalty=penalty,
    )


def terrain_meta(grid: TerrainGrid) -> TerrainMeta:
    return TerrainMeta(
        lat_min=grid.lat_min,
        lat_max=grid.lat_max,
        lon_min=grid.lon_min,
        lon_max=grid.lon_max,
        cell_km=grid.cell_km,
        rows=grid.rows,
        cols=grid.cols,
        elev_min_m=float(grid.elev_m.min()),
        elev_max_m=float(grid.elev_m.max()),
    )


def grid_shape(bbox: BoundingBox) -> tuple[int, int]:
    mid_lat = (bbox.lat_min + bbox.lat_max) / 2
    mid_lon = (bbox.lon_min + bbox.lon_max) / 2
    north_m = great_circle_km(bbox.lat_min, mid_lon, bbox.lat_max, mid_lon) * 1000
    east_m = great_circle_km(mid_lat, bbox.lon_min, mid_lat, bbox.lon_max) * 1000
    rows = max(1, int(round(north_m / (CELL_KM * 1000))))
    cols = max(1, int(round(east_m / (CELL_KM * 1000))))
    return rows, cols


def _capacity(rng: np.random.Generator, runway_length_m: int) -> tuple[int, int, int, float]:
    scale = runway_length_m / 2743.0
    shelter = int(min(40, max(8, round(int(rng.integers(12, 29)) * scale))))
    fuel = int(min(2_500_000, max(400_000, round(int(rng.integers(700_000, 1_500_001)) * scale))))
    crews = int(min(20, max(2, round(int(rng.integers(4, 13)) * scale))))
    sorties = round(min(10.0, max(1.5, float(rng.uniform(2.5, 6.5)) * scale)), 1)
    return shelter, fuel, crews, sorties


def _roads(places: list[GeoFeature]) -> list[GeoFeature]:
    cities = [place for place in places if place.kind == "city"]
    points = [(city, _point(city.wkt)) for city in cities]
    seen: set[tuple[str, str]] = set()
    roads: list[GeoFeature] = []
    for city, origin in points:
        neighbours = sorted(
            (
                great_circle_km(origin.y, origin.x, other.y, other.x),
                other_city,
                other,
            )
            for other_city, other in points
            if other_city.feature_id != city.feature_id
        )
        linked = 0
        for distance_km, other_city, other in neighbours:
            if linked >= ROAD_LINKS or distance_km > ROAD_LIMIT_KM:
                break
            pair = tuple(sorted((city.feature_id, other_city.feature_id)))
            if pair in seen:
                linked += 1
                continue
            seen.add(pair)
            linked += 1
            left, right = sorted((city, other_city), key=lambda item: item.feature_id)
            start = _point(left.wkt)
            end = _point(right.wkt)
            roads.append(
                GeoFeature(
                    feature_id=f"road:{left.feature_id.removeprefix('city:')}-{right.feature_id.removeprefix('city:')}",
                    kind="road",
                    name=f"{left.name}-{right.name}",
                    wkt=LineString([(start.x, start.y), (end.x, end.y)]).wkt,
                )
            )
    return roads


def _slope_deg(bbox: BoundingBox, elev_m: np.ndarray) -> np.ndarray:
    rows, cols = elev_m.shape
    mid_lat = (bbox.lat_min + bbox.lat_max) / 2
    mid_lon = (bbox.lon_min + bbox.lon_max) / 2
    dy_m = great_circle_km(bbox.lat_min, mid_lon, bbox.lat_max, mid_lon) * 1000 / rows
    dx_m = great_circle_km(mid_lat, bbox.lon_min, mid_lat, bbox.lon_max) * 1000 / cols
    north, east = np.gradient(elev_m, dy_m, dx_m)
    return np.degrees(np.arctan(np.hypot(north, east)))


def _airport_idents(path: Path) -> list[str]:
    with path.open(newline="") as handle:
        return [row["ident"] for row in csv.DictReader(handle)]


def _as_multiline(geom: BaseGeometry) -> MultiLineString | None:
    if geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    if geom.geom_type == "GeometryCollection":
        lines = []
        for part in geom.geoms:
            if part.geom_type == "LineString":
                lines.append(part)
            elif part.geom_type == "MultiLineString":
                lines.extend(part.geoms)
        if not lines:
            return None
        return MultiLineString(lines)
    return None


def _point(text: str) -> Point:
    geom = shapely_wkt.loads(text)
    if geom.geom_type != "Point":
        raise ValueError("city geometry must be a point")
    return geom
