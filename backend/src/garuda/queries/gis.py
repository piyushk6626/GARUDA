"""Distances, buffers, and intersections over the seeded geography."""

from dataclasses import dataclass

from pyproj import CRS, Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from garuda.bases import BASES
from garuda.generators.gis import great_circle_km
from garuda.schemas.gis import (
    BaseRecord,
    BaseTargetDistance,
    BorderFeature,
    DistanceMatrixQuery,
    DistanceMatrixResult,
    GeoFeature,
    GeoRef,
    SpatialHit,
    SpatialQuery,
    SpatialResult,
)
from garuda.schemas.mission import Target

HIT_LIMIT = 50


@dataclass(frozen=True)
class _Place:
    entity_id: str
    kind: str
    lat: float
    lon: float
    geom: BaseGeometry


def spatial_query(
    query: SpatialQuery,
    bases: list[BaseRecord],
    features: list[GeoFeature],
    borders: list[BorderFeature],
    targets: list[Target] | None = None,
) -> SpatialResult:
    catalog = _catalog(bases, features, borders, targets or [])
    origin = _resolve(query.a, catalog, bases)
    if query.op == "distance_km":
        if query.b is None:
            raise ValueError("distance_km needs a second geometry")
        other = _resolve(query.b, catalog, bases)
        return SpatialResult(op=query.op, distance_km=_between(origin, other))
    if query.op == "within_radius":
        if query.radius_km is None:
            raise ValueError("within_radius needs radius_km")
        return SpatialResult(op=query.op, hits=_within(origin, catalog, query.radius_km))
    if query.op == "nearest":
        return SpatialResult(op=query.op, hits=_nearest(origin, catalog))
    if query.op == "intersects":
        if query.b is None:
            raise ValueError("intersects needs a second geometry")
        other = _resolve(query.b, catalog, bases)
        hits = []
        if origin.geom.intersects(other.geom):
            hits.append(_hit(other, _between(origin, other)))
        return SpatialResult(op=query.op, hits=hits)
    if query.op == "buffer":
        if query.radius_km is None:
            raise ValueError("buffer needs radius_km")
        polygon = _buffer(origin.lat, origin.lon, query.radius_km)
        hits: list[SpatialHit] = []
        for place in catalog:
            if place.entity_id == origin.entity_id or not polygon.intersects(place.geom):
                continue
            metres, lat, lon = _separation(origin, place)
            hits.append(_hit(place, metres / 1000, lat, lon))
        hits.sort(key=lambda hit: (hit.distance_km or 0, hit.entity_id))
        return SpatialResult(op=query.op, hits=hits[:HIT_LIMIT], wkt=polygon.wkt)
    raise ValueError(f"unknown spatial op {query.op}")


def distance_matrix(
    distances: list[BaseTargetDistance],
    query: DistanceMatrixQuery,
) -> DistanceMatrixResult:
    known = {base.base_id for base in BASES}
    index = {(row.base_id, row.target_id): row.distance_km for row in distances}
    matrix: list[list[float]] = []
    for base_id in query.base_ids:
        if base_id not in known:
            valid = ", ".join(base.base_id for base in BASES)
            raise ValueError(f"unknown base_id {base_id}; valid: {valid}")
        row: list[float] = []
        for target_id in query.target_ids:
            key = (base_id, target_id)
            if key not in index:
                raise ValueError(f"no distance for {base_id} to {target_id}")
            row.append(index[key])
        matrix.append(row)
    return DistanceMatrixResult(
        base_ids=list(query.base_ids),
        target_ids=list(query.target_ids),
        kilometres=matrix,
    )


def _catalog(
    bases: list[BaseRecord],
    features: list[GeoFeature],
    borders: list[BorderFeature],
    targets: list[Target],
) -> list[_Place]:
    places = [
        _Place(base.base_id, "base", base.lat, base.lon, Point(base.lon, base.lat))
        for base in bases
    ]
    places.extend(
        _Place(target.target_id, "target", target.lat, target.lon, Point(target.lon, target.lat))
        for target in targets
    )
    for feature in features:
        geom = shapely_wkt.loads(feature.wkt)
        places.append(_Place(feature.feature_id, feature.kind, geom.centroid.y, geom.centroid.x, geom))
    for border in borders:
        geom = shapely_wkt.loads(border.wkt)
        places.append(_Place(border.border_id, border.kind, geom.centroid.y, geom.centroid.x, geom))
    return places


def _resolve(ref: GeoRef, catalog: list[_Place], bases: list[BaseRecord]) -> _Place:
    if ref.lat is not None and ref.lon is not None:
        label = ref.entity_id or f"{ref.lat:.4f},{ref.lon:.4f}"
        return _Place(label, "point", ref.lat, ref.lon, Point(ref.lon, ref.lat))
    if ref.entity_id is None:
        raise ValueError("geometry needs an entity_id or coordinates")
    for place in catalog:
        if place.entity_id == ref.entity_id:
            return place
    if ref.entity_id.startswith("B"):
        valid = ", ".join(base.base_id for base in bases) or ", ".join(base.base_id for base in BASES)
        raise ValueError(f"unknown base_id {ref.entity_id}; valid: {valid}")
    raise ValueError(f"unknown entity {ref.entity_id}")


def _within(origin: _Place, catalog: list[_Place], radius_km: float) -> list[SpatialHit]:
    hits = []
    for place in catalog:
        metres, lat, lon = _separation(origin, place)
        distance_km = metres / 1000
        if distance_km <= radius_km:
            hits.append(_hit(place, distance_km, lat, lon))
    hits.sort(key=lambda hit: (hit.distance_km or 0, hit.entity_id))
    return hits[:HIT_LIMIT]


def _nearest(origin: _Place, catalog: list[_Place]) -> list[SpatialHit]:
    others = [place for place in catalog if place.entity_id != origin.entity_id]
    if not others:
        raise ValueError("nearest needs another geometry")
    place, metres, lat, lon = min(
        ((item, *_separation(origin, item)) for item in others),
        key=lambda row: row[1],
    )
    return [_hit(place, metres / 1000, lat, lon)]


def _between(left: _Place, right: _Place) -> float:
    metres, _lat, _lon = _separation(left, right)
    return round(metres / 1000, 3)


def _separation(origin: _Place, place: _Place) -> tuple[float, float, float]:
    if place.geom.geom_type == "Point" and origin.geom.geom_type == "Point":
        metres = great_circle_km(origin.lat, origin.lon, place.lat, place.lon) * 1000
        return metres, place.lat, place.lon
    forward = _transformer(origin.lat, origin.lon)
    backward = Transformer.from_crs(_azimuthal(origin.lat, origin.lon), "EPSG:4326", always_xy=True)
    local_origin = _project(origin.geom, forward)
    local_place = _project(place.geom, forward)
    closest = nearest_points(local_origin, local_place)[1]
    lon, lat = backward.transform(closest.x, closest.y)
    return float(local_origin.distance(local_place)), float(lat), float(lon)


def _buffer(lat: float, lon: float, radius_km: float) -> Polygon:
    forward = _transformer(lat, lon)
    backward = Transformer.from_crs(_azimuthal(lat, lon), "EPSG:4326", always_xy=True)
    local = _project(Point(lon, lat), forward)
    circle = local.buffer(radius_km * 1000)
    ring = [backward.transform(x, y) for x, y in circle.exterior.coords]
    return Polygon(ring)


def _transformer(lat: float, lon: float) -> Transformer:
    return Transformer.from_crs("EPSG:4326", _azimuthal(lat, lon), always_xy=True)


def _azimuthal(lat: float, lon: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def _project(geom: BaseGeometry, forward: Transformer) -> BaseGeometry:
    if geom.geom_type == "Point":
        return Point(forward.transform(geom.x, geom.y))
    if geom.geom_type == "LineString":
        return LineString([forward.transform(x, y) for x, y in geom.coords])
    if geom.geom_type == "MultiLineString":
        return MultiLineString(
            [LineString([forward.transform(x, y) for x, y in line.coords]) for line in geom.geoms]
        )
    if geom.geom_type == "Polygon":
        return Polygon([forward.transform(x, y) for x, y in geom.exterior.coords])
    return geom


def _hit(place: _Place, distance_km: float, lat: float | None = None, lon: float | None = None) -> SpatialHit:
    return SpatialHit(
        entity_id=place.entity_id,
        kind=place.kind,
        lat=place.lat if lat is None else lat,
        lon=place.lon if lon is None else lon,
        distance_km=round(distance_km, 3),
    )
