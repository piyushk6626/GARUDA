"""Synthetic Red-force threats.

Mobile units walk a short border polyline. That polyline stands in for OSM
roads until geo_feature is loaded. The exposure raster is computed in memory;
MinIO storage waits for the GIS grid.
"""

import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, cast

import numpy as np

from garuda.config import ScenarioConfig
from garuda.schemas.mission import Mission, Target
from garuda.schemas.threat import Threat, ThreatTrack, ThreatType

SAM_TYPES = frozenset({"SAM_LONG", "SAM_MED", "SAM_SHORT"})
FIXED_TYPES = (
    "SAM_LONG",
    "SAM_MED",
    "SAM_SHORT",
    "RADAR_EW",
    "RADAR_GCI",
    "AAA",
    "AIRFIELD",
)
ENGAGEMENT_KM = {
    "SAM_LONG": (150.0, 250.0),
    "SAM_MED": (40.0, 80.0),
    "SAM_SHORT": (5.0, 20.0),
    "AAA": (5.0, 20.0),
    "AIRFIELD": (5.0, 20.0),
    "ENEMY_CAP": (40.0, 80.0),
}
RADAR_DETECTION_KM = {
    "RADAR_EW": (250.0, 400.0),
    "RADAR_GCI": (150.0, 300.0),
}
ALTITUDE_FT = {
    "SAM_LONG": (1000, 80_000),
    "SAM_MED": (100, 60_000),
    "SAM_SHORT": (50, 20_000),
    "AAA": (0, 12_000),
    "AIRFIELD": (0, 15_000),
    "ENEMY_CAP": (500, 50_000),
    "RADAR_EW": (0, 80_000),
    "RADAR_GCI": (0, 80_000),
}
PK_BAND = {
    "SAM_LONG": (0.60, 0.85),
    "SAM_MED": (0.50, 0.75),
    "SAM_SHORT": (0.40, 0.70),
    "AAA": (0.15, 0.40),
    "AIRFIELD": (0.10, 0.30),
    "ENEMY_CAP": (0.30, 0.60),
}
SPEED_KTS = {
    "SAM_MED": (15.0, 35.0),
    "SAM_SHORT": (15.0, 35.0),
    "ENEMY_CAP": (300.0, 480.0),
}
FORWARD_BASES = {"B2": (26.89, 70.86)}
CLUSTER_COUNT = 3
WEST_LON_MAX = 72.9
MOBILE_TAU_HOURS = 12.0
FIXED_TAU_HOURS = 72.0


class ThreatCluster(NamedTuple):
    cluster_id: int
    lat: float
    lon: float
    threat_ids: tuple[str, ...]


class _Assigned(NamedTuple):
    cluster_id: int
    threat: Threat


def build_threats(
    config: ScenarioConfig,
    rng: np.random.Generator,
) -> tuple[list[Threat], list[ThreatTrack], list[ThreatCluster]]:
    start = _as_utc(config.start)
    centers = _cluster_centers(rng)
    fixed = _fixed_threats(config, rng, centers, start)
    mobile = _mobile_threats(config, rng, centers, start)
    tracks = _tracks(config, rng, mobile, centers, start)
    located = _locate_at_track_start(mobile, tracks)
    popups = _popup_threats(config, rng, start)
    threats = [item.threat for item in fixed] + located + popups
    return threats, tracks, _clusters(centers, fixed, mobile)


def confidence_at(threat: Threat, moment: datetime) -> float:
    elapsed_hours = (_as_utc(moment) - _as_utc(threat.last_seen)).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return threat.confidence
    tau_hours = MOBILE_TAU_HOURS if threat.mobile else FIXED_TAU_HOURS
    return threat.confidence * math.exp(-elapsed_hours / tau_hours)


def exposure_risk(lon: float, lat: float, alt_ft: float, threats: list[Threat]) -> float:
    if not threats:
        return 0.0
    survival = 1.0
    for threat in threats:
        if not _inside_envelope(lon, lat, alt_ft, threat):
            continue
        survival *= 1.0 - threat.pk_base * threat.confidence
    return 1.0 - survival


def suppress_sead_site(threats: list[Threat], missions: list[Mission], targets: list[Target]) -> None:
    by_id = {threat.threat_id: threat for threat in threats}
    target_by_id = {target.target_id: target for target in targets}
    for mission in sorted(missions, key=lambda item: item.mission_id):
        if mission.type != "SEAD" or mission.target_id is None:
            continue
        target = target_by_id.get(mission.target_id)
        if target is None:
            continue
        for threat_id in target.defended_by:
            threat = by_id.get(threat_id)
            if threat is None or threat.mobile or threat.status != "ACTIVE":
                continue
            if threat.type not in SAM_TYPES:
                continue
            threat.status = "SUPPRESSED"
            return
    raise RuntimeError("no fixed SAM defends a SEAD target")


def _fixed_threats(
    config: ScenarioConfig,
    rng: np.random.Generator,
    centers: list[tuple[float, float]],
    start: datetime,
) -> list[_Assigned]:
    count = int(rng.integers(12, 21))
    local = [0 for _ in centers]
    assigned: list[_Assigned] = []
    for index in range(count):
        cluster_id = index % len(centers)
        threat_type = FIXED_TYPES[index % len(FIXED_TYPES)]
        center_lat, center_lon = centers[cluster_id]
        lon, lat = _place(
            center_lat + float(rng.normal(0, 0.12)),
            center_lon + float(rng.normal(0, 0.12)),
            config,
        )
        assigned.append(
            _Assigned(
                cluster_id,
                _threat(
                    threat_id=f"TH-{cluster_id:02d}-{local[cluster_id]:02d}",
                    threat_type=threat_type,
                    lon=lon,
                    lat=lat,
                    mobile=False,
                    rng=rng,
                    first_seen=start - timedelta(hours=float(rng.uniform(6.0, 48.0))),
                    last_seen=start,
                ),
            )
        )
        local[cluster_id] += 1
    return assigned


def _mobile_threats(
    config: ScenarioConfig,
    rng: np.random.Generator,
    centers: list[tuple[float, float]],
    start: datetime,
) -> list[_Assigned]:
    return _moving(
        config,
        rng,
        centers,
        start,
        count=int(rng.integers(2, 5)),
        type_for=lambda index: "SAM_MED" if float(rng.random()) < 0.5 else "SAM_SHORT",
        ident=lambda index: f"TH-M{index:02d}",
    ) + _moving(
        config,
        rng,
        centers,
        start,
        count=int(rng.integers(1, 3)),
        type_for=lambda index: "ENEMY_CAP",
        ident=lambda index: f"TH-C{index:02d}",
    )


def _moving(
    config: ScenarioConfig,
    rng: np.random.Generator,
    centers: list[tuple[float, float]],
    start: datetime,
    count: int,
    type_for: Callable[[int], str],
    ident: Callable[[int], str],
) -> list[_Assigned]:
    assigned: list[_Assigned] = []
    for index in range(count):
        cluster_id = index % len(centers)
        center_lat, center_lon = centers[cluster_id]
        lon, lat = _place(center_lat, center_lon, config)
        assigned.append(
            _Assigned(
                cluster_id,
                _threat(
                    threat_id=ident(index),
                    threat_type=type_for(index),
                    lon=lon,
                    lat=lat,
                    mobile=True,
                    rng=rng,
                    first_seen=start - timedelta(hours=float(rng.uniform(6.0, 48.0))),
                    last_seen=start,
                ),
            )
        )
    return assigned


def _popup_threats(
    config: ScenarioConfig,
    rng: np.random.Generator,
    start: datetime,
) -> list[Threat]:
    popups: list[Threat] = []
    for number, event in enumerate((item for item in config.events if item.type == "new_sam"), start=1):
        anchor_lat, anchor_lon = FORWARD_BASES.get(event.near or "B2", FORWARD_BASES["B2"])
        lon, lat = _place(
            anchor_lat + float(rng.uniform(-0.15, 0.15)),
            anchor_lon + float(rng.uniform(-0.15, 0.15)),
            config,
        )
        moment = start + timedelta(hours=event.t)
        popups.append(
            _threat(
                threat_id=f"TH-POP-{number:03d}",
                threat_type="SAM_MED",
                lon=lon,
                lat=lat,
                mobile=False,
                rng=rng,
                first_seen=moment,
                last_seen=moment,
            )
        )
    return popups


def _tracks(
    config: ScenarioConfig,
    rng: np.random.Generator,
    mobile: list[_Assigned],
    centers: list[tuple[float, float]],
    start: datetime,
) -> list[ThreatTrack]:
    tracks: list[ThreatTrack] = []
    hours = int(config.horizon_hours)
    for assigned in mobile:
        speed = float(rng.uniform(*SPEED_KTS[assigned.threat.type]))
        center_lat, center_lon = centers[assigned.cluster_id]
        road = _road(center_lat, center_lon, rng, config)
        tracks.extend(_walk(assigned.threat.threat_id, road, rng, start, hours, speed, config))
    return tracks


def _road(
    center_lat: float,
    center_lon: float,
    rng: np.random.Generator,
    config: ScenarioConfig,
) -> list[tuple[float, float]]:
    lats = np.linspace(center_lat - 0.35, center_lat + 0.35, 8)
    jitters = rng.normal(0, 0.03, size=8)
    return [
        _place(float(lat), float(center_lon + jitter), config)
        for lat, jitter in zip(lats, jitters, strict=True)
    ]


def _walk(
    threat_id: str,
    road: list[tuple[float, float]],
    rng: np.random.Generator,
    start: datetime,
    hours: int,
    speed: float,
    config: ScenarioConfig,
) -> list[ThreatTrack]:
    vertex_count = len(road)
    cursor = 0
    direction = 1
    previous: tuple[float, float] | None = None
    samples: list[ThreatTrack] = []
    for hour in range(hours + 1):
        base_lon, base_lat = road[cursor]
        lon, lat = _place(
            base_lat + float(rng.normal(0, 0.008)),
            base_lon + float(rng.normal(0, 0.008)),
            config,
        )
        if previous is None:
            ahead = road[1] if vertex_count > 1 else road[0]
            heading = _heading(base_lon, base_lat, ahead[0], ahead[1])
        else:
            heading = _heading(previous[0], previous[1], lon, lat)
        samples.append(
            ThreatTrack(
                time=start + timedelta(hours=hour),
                threat_id=threat_id,
                lon=lon,
                lat=lat,
                speed_kts=round(speed, 1),
                heading=_bounded_heading(heading),
            )
        )
        previous = (lon, lat)
        cursor += direction
        if cursor < 0 or cursor >= vertex_count:
            direction *= -1
            cursor += direction
    return samples


def _locate_at_track_start(assigned: list[_Assigned], tracks: list[ThreatTrack]) -> list[Threat]:
    origin: dict[str, ThreatTrack] = {}
    for track in tracks:
        origin.setdefault(track.threat_id, track)
    located: list[Threat] = []
    for item in assigned:
        point = origin[item.threat.threat_id]
        located.append(item.threat.model_copy(update={"lon": point.lon, "lat": point.lat}))
    return located


def _clusters(
    centers: list[tuple[float, float]],
    fixed: list[_Assigned],
    mobile: list[_Assigned],
) -> list[ThreatCluster]:
    grouped: list[list[Threat]] = [[] for _ in centers]
    for assigned in fixed + mobile:
        grouped[assigned.cluster_id].append(assigned.threat)
    clusters: list[ThreatCluster] = []
    for cluster_id, (lat, lon) in enumerate(centers):
        group = grouped[cluster_id]
        sams = [threat for threat in group if threat.type in SAM_TYPES]
        others = [threat for threat in group if threat.type not in SAM_TYPES]
        if not sams:
            raise RuntimeError(f"cluster {cluster_id} has no SAM")
        clusters.append(
            ThreatCluster(
                cluster_id,
                lat,
                lon,
                tuple(threat.threat_id for threat in sams + others),
            )
        )
    return clusters


def _cluster_centers(rng: np.random.Generator) -> list[tuple[float, float]]:
    lats = rng.uniform(24.0, 31.5, size=CLUSTER_COUNT)
    lons = rng.uniform(69.0, 72.0, size=CLUSTER_COUNT)
    return [(float(lats[index]), float(lons[index])) for index in range(CLUSTER_COUNT)]


def _threat(
    threat_id: str,
    threat_type: str,
    lon: float,
    lat: float,
    mobile: bool,
    rng: np.random.Generator,
    first_seen: datetime,
    last_seen: datetime,
) -> Threat:
    engagement, detection, pk = _draw_profile(threat_type, rng)
    min_alt, max_alt = ALTITUDE_FT[threat_type]
    return Threat(
        threat_id=threat_id,
        type=cast(ThreatType, threat_type),
        lon=lon,
        lat=lat,
        mobile=mobile,
        engagement_range_km=engagement,
        detection_range_km=detection,
        min_alt_ft=min_alt,
        max_alt_ft=max_alt,
        pk_base=pk,
        confidence=round(float(rng.uniform(0.45, 0.95)), 3),
        first_seen=first_seen,
        last_seen=last_seen,
        status="ACTIVE",
    )


def _draw_profile(threat_type: str, rng: np.random.Generator) -> tuple[float, float, float]:
    if threat_type in RADAR_DETECTION_KM:
        low, high = RADAR_DETECTION_KM[threat_type]
        return 0.0, round(float(rng.uniform(low, high)), 2), 0.0
    low, high = ENGAGEMENT_KM[threat_type]
    engagement = round(float(rng.uniform(low, high)), 2)
    detection = round(engagement * float(rng.uniform(1.1, 1.5)), 2)
    pk_low, pk_high = PK_BAND[threat_type]
    return engagement, max(detection, engagement), round(float(rng.uniform(pk_low, pk_high)), 3)


def _inside_envelope(lon: float, lat: float, alt_ft: float, threat: Threat) -> bool:
    if alt_ft < threat.min_alt_ft or alt_ft > threat.max_alt_ft:
        return False
    return _distance_km(lon, lat, threat.lon, threat.lat) <= threat.engagement_range_km


def _distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    earth_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_km * math.asin(min(1.0, math.sqrt(haversine)))


def _heading(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    delta_lon = math.radians(lon2 - lon1)
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    east = math.sin(delta_lon) * math.cos(lat2_rad)
    north = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(
        delta_lon
    )
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def _bounded_heading(raw: float) -> float:
    heading = round(float(raw) % 360.0, 2)
    if heading >= 360.0 or heading < 0.0:
        return 0.0
    return heading


def _place(lat: float, lon: float, config: ScenarioConfig) -> tuple[float, float]:
    west_max = min(config.bbox.lon_max, WEST_LON_MAX)
    return (
        round(_clip(lon, config.bbox.lon_min, west_max), 5),
        round(_clip(lat, config.bbox.lat_min, config.bbox.lat_max), 5),
    )


def _clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
