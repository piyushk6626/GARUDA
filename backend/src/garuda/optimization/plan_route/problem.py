"""Read-only preparation for routing; public schemas remain unchanged."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import cos, exp, isfinite, pi

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely import wkt
from shapely.geometry import Polygon
from shapely.ops import transform

from garuda.contracts.tool import ToolInputError
from garuda.optimization.plan_route.schemas import PlanRouteInput
from garuda.schemas.aircraft import AircraftTypeSpec, canonical_type
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.gis import TerrainMeta
from garuda.schemas.weather import WeatherCell
from garuda.storage.gis_files import DEM_NPY, PENALTY_NPY, gis_root

GEOD = Geod(ellps="WGS84")
ALTITUDES = {"LOW": 3000, "MEDIUM": 18000, "HIGH": 35000}


@dataclass(frozen=True)
class RiskSource:
    threat_id: str
    x: float
    y: float
    radius: float
    probability: float
    min_alt_ft: int
    max_alt_ft: int
    geometry: object


@dataclass
class RouteProblem:
    payload: PlanRouteInput
    aircraft: AircraftTypeSpec
    forward: Transformer
    backward: Transformer
    goal: tuple[float, float]
    altitudes: tuple[int, ...]
    threats: tuple[RiskSource, ...]
    zones: tuple[tuple[AirspaceZone, object], ...]
    weather: dict[tuple[float, float], list[tuple[datetime, WeatherCell]]]
    weather_locations: np.ndarray
    traffic: np.ndarray
    terrain: TerrainMeta
    elevation: np.ndarray | None
    terrain_penalty: np.ndarray | None
    seed: int
    notes: list[str] = field(default_factory=list)

    def latlon(self, points):
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        lon, lat = self.backward.transform(points[:, 0] * 1000, points[:, 1] * 1000)
        return np.column_stack((lat, lon))


def _aware(moment, name):
    if moment.utcoffset() is None:
        raise ToolInputError(f"{name} must be timezone-aware")


def build_problem(payload, ctx) -> RouteProblem:
    """Capture one scenario snapshot; never call another tool/provider recursively."""
    _aware(payload.depart_time, "depart_time")
    for name in ("threat", "weather", "civil", "terrain"):
        value = getattr(payload.weights, name)
        if not isfinite(value) or value < 0:
            raise ToolInputError(f"weights.{name} must be finite and non-negative")
    if not isfinite(payload.max_risk) or not 0 <= payload.max_risk <= 1:
        raise ToolInputError("max_risk must be finite and in [0, 1]")
    for point in (payload.origin, payload.destination):
        if not isfinite(point.lat) or not isfinite(point.lon):
            raise ToolInputError("Route coordinates must be finite")
    try:
        aircraft_type = canonical_type(payload.aircraft_type)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    scenario = ctx.scenario.snapshot()
    specs = [s for s in scenario.aircraft_types if s.type == aircraft_type]
    if len(specs) != 1:
        raise ToolInputError(f"Expected one aircraft type specification for {aircraft_type}")
    aircraft = specs[0]
    for name in ("cruise_kts", "range_km", "fuel_capacity_kg", "ceiling_ft"):
        if not isfinite(getattr(aircraft, name)) or getattr(aircraft, name) <= 0:
            raise ToolInputError(f"Aircraft {name} must be finite and positive")
    bands = tuple(ALTITUDES.values()) if payload.altitude_profile == "AUTO" else (
        ALTITUDES[payload.altitude_profile],
    )
    altitudes = tuple(h for h in bands if h <= aircraft.ceiling_ft)
    if not altitudes:
        raise ToolInputError("Requested altitude profile exceeds the aircraft ceiling")
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={payload.origin.lat} +lon_0={payload.origin.lon} "
        "+datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", local, always_xy=True)
    backward = Transformer.from_crs(local, "EPSG:4326", always_xy=True)
    gx, gy = forward.transform(payload.destination.lon, payload.destination.lat)
    goal = gx / 1000, gy / 1000
    if not all(isfinite(v) for v in goal):
        raise ToolInputError("Route cannot be projected into a local planning grid")
    if np.hypot(*goal) > aircraft.range_km + 1e-8:
        raise ToolInputError("Origin-to-destination distance exceeds aircraft range; AAR is not specified")

    threats = []
    ids = set()
    for threat in sorted(scenario.threats, key=lambda t: t.threat_id):
        if threat.threat_id in ids:
            raise ToolInputError(f"Duplicate threat_id: {threat.threat_id}")
        ids.add(threat.threat_id)
        _aware(threat.first_seen, "threat.first_seen")
        _aware(threat.last_seen, "threat.last_seen")
        if threat.status == "DESTROYED" or threat.first_seen > payload.depart_time:
            continue
        lon, lat = threat.lon, threat.lat
        observed = threat.last_seen
        # Use only sightings already available at departure, never future tracks.
        if threat.mobile:
            for track in scenario.tracks:
                if track.threat_id == threat.threat_id:
                    _aware(track.time, "threat_track.time")
            tracks = [t for t in scenario.tracks if t.threat_id == threat.threat_id
                      and t.time <= payload.depart_time]
            if tracks:
                latest = max(tracks, key=lambda t: t.time)
                lon, lat = latest.lon, latest.lat
                observed = latest.time
        if not isfinite(lon) or not isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ToolInputError(f"Invalid threat coordinates: {threat.threat_id}")
        age = max(0, (payload.depart_time - observed).total_seconds() / 3600)
        confidence = threat.confidence * exp(-age / (12 if threat.mobile else 72))
        # SUPPRESSED already has a reduced pk_base in the scenario generator.
        probability = threat.pk_base * confidence
        if probability == 0 or threat.engagement_range_km == 0:
            continue
        x, y = forward.transform(lon, lat)
        if not all(isfinite(v) for v in (x, y, probability)):
            raise ToolInputError(f"Invalid threat coordinates/probability: {threat.threat_id}")
        # A geodesic ring keeps lethal range correct away from the projection
        # origin. Slightly circumscribe it so polygon chords do not cut corners.
        bearings = np.arange(360, dtype=float)
        ring_lon, ring_lat, _ = GEOD.fwd(
            np.full(360, lon), np.full(360, lat), bearings,
            np.full(360, threat.engagement_range_km * 1000 / cos(pi / 360)),
        )
        ring_x, ring_y = forward.transform(ring_lon, ring_lat)
        geometry = Polygon(np.column_stack((np.asarray(ring_x) / 1000,
                                            np.asarray(ring_y) / 1000))).buffer(.001)
        if not geometry.is_valid or not all(isfinite(v) for v in geometry.bounds):
            raise ToolInputError(f"Invalid projected threat envelope: {threat.threat_id}")
        radius = float(np.max(np.hypot(np.asarray(ring_x) / 1000 - x / 1000,
                                      np.asarray(ring_y) / 1000 - y / 1000))) + .002
        threats.append(RiskSource(threat.threat_id, x / 1000, y / 1000,
                                  radius, probability, threat.min_alt_ft,
                                  threat.max_alt_ft, geometry))

    zones = []
    ids = set()
    for zone in sorted(scenario.zones, key=lambda z: z.zone_id):
        if zone.zone_id in ids:
            raise ToolInputError(f"Duplicate zone_id: {zone.zone_id}")
        ids.add(zone.zone_id)
        try:
            geom = wkt.loads(zone.wkt)
            if geom.is_empty or not geom.is_valid or geom.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("expected a valid polygon")
            def project(x, y, z=None):
                px, py = forward.transform(x, y)
                return np.asarray(px) / 1000, np.asarray(py) / 1000
            # WKT edges are straight in geographic coordinates. Densify before
            # projection so a long latitude edge is not replaced by a shifted
            # chord in the metric grid. A one-metre buffer covers interpolation.
            geom = transform(project, geom.segmentize(.005)).buffer(.001)
            if not geom.is_valid or not all(isfinite(v) for v in geom.bounds):
                raise ValueError("invalid projected polygon")
        except Exception as exc:
            raise ToolInputError(f"Invalid airspace geometry for {zone.zone_id}: {exc}") from exc
        zones.append((zone, geom))

    weather = {}
    for cell in scenario.cells:
        _aware(cell.time, "weather.time")
        _aware(cell.issued_at, "weather.issued_at")
        if cell.issued_at > payload.depart_time:
            continue
        if not isfinite(cell.lat) or not isfinite(cell.lon) or not -90 <= cell.lat <= 90 or not -180 <= cell.lon <= 180:
            raise ToolInputError("Invalid weather coordinates")
        key = (cell.lat, cell.lon)
        bucket = weather.setdefault(key, {})
        old = bucket.get(cell.time)
        if old is None or old.issued_at < cell.issued_at:
            bucket[cell.time] = cell
    locations = np.asarray(sorted(weather), dtype=float).reshape(-1, 2)
    weather = {key: sorted(values.items()) for key, values in weather.items()}

    traffic = {}
    for track in scenario.adsb_tracks:
        if track.on_ground or not payload.depart_time - timedelta(minutes=15) <= track.time <= payload.depart_time:
            continue
        if track.icao24 not in traffic or traffic[track.icao24].time < track.time:
            traffic[track.icao24] = track
    traffic_points = []
    for track in traffic.values():
        x, y = forward.transform(track.lon, track.lat)
        alt = track.geo_alt_m if track.geo_alt_m is not None else track.alt_m
        traffic_points.append((x / 1000, y / 1000, np.nan if alt is None else alt / .3048))

    terrain = scenario.terrain
    if (not all(isfinite(getattr(terrain, n)) for n in
                ("lat_min", "lat_max", "lon_min", "lon_max", "elev_min_m", "elev_max_m"))
            or terrain.lat_min >= terrain.lat_max or terrain.lon_min >= terrain.lon_max
            or terrain.elev_min_m > terrain.elev_max_m):
        raise ToolInputError("Invalid terrain metadata")
    elevation = penalty = None
    notes = []
    root = gis_root()
    if (root / DEM_NPY).exists():
        try:
            elevation = np.load(root / DEM_NPY, allow_pickle=False)
            if (elevation.shape != (terrain.rows, terrain.cols) or not np.isfinite(elevation).all()
                    or float(elevation.min()) < terrain.elev_min_m - 1
                    or float(elevation.max()) > terrain.elev_max_m + 1):
                raise ValueError("DEM does not match snapshot terrain metadata")
            if (root / PENALTY_NPY).exists():
                penalty = np.load(root / PENALTY_NPY, allow_pickle=False)
                if (penalty.shape != elevation.shape or not np.isfinite(penalty).all()
                        or np.any((penalty < 0) | (penalty > 1))):
                    raise ValueError("invalid terrain penalty grid")
        except Exception as exc:
            raise ToolInputError(f"Cannot read terrain grid: {exc}") from exc
    else:
        notes.append("DEM unavailable: using the snapshot maximum elevation throughout the terrain extent.")
    if not weather:
        if scenario.cells:
            raise ToolInputError("No weather forecast was issued by depart_time")
        notes.append("No weather cells in snapshot; weather cost is neutral.")
    return RouteProblem(payload, aircraft, forward, backward, goal, altitudes,
                        tuple(threats), tuple(zones), weather, locations,
                        np.asarray(traffic_points).reshape(-1, 3), terrain,
                        elevation, penalty, ctx.seed, notes)
