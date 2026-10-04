"""Launch odds, threat exposure, and loss probability shared by the simulation tools."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from garuda.common import as_utc
from garuda.optimization.common.geo import haversine_km

Point = tuple[float, float]

P_LAUNCH = {"FMC": 0.97, "PMC": 0.88, "IN_MISSION": 0.9, "NMC": 0.0, "AOG": 0.0}
P_LAUNCH_AFTER_REPAIR = 0.85
THREAT_WEIGHT = {"ACTIVE": 1.0, "UNKNOWN": 0.6, "SUPPRESSED": 0.25, "DESTROYED": 0.0}
SAMPLE_KM = 5.0
PK_CAP = 0.95


@dataclass(frozen=True)
class ThreatRing:
    threat_id: str
    lat: float
    lon: float
    range_km: float
    pk: float
    confidence: float
    min_alt_ft: int = 0
    max_alt_ft: int = 100_000


def launch_probability(aircraft, depart: datetime) -> float:
    """Chance the tail launches at `depart`: status odds, or repaired-by-ETA odds when grounded."""
    base = P_LAUNCH.get(aircraft.status, 0.0)
    if base > 0:
        return base
    eta = getattr(aircraft, "maint_return_eta", None)
    if eta is not None and as_utc(eta) <= as_utc(depart):
        return P_LAUNCH_AFTER_REPAIR
    return 0.0


def threat_rings(threats) -> list[ThreatRing]:
    rings = []
    for threat in threats:
        weight = THREAT_WEIGHT.get(threat.status, 0.0)
        if weight <= 0 or threat.engagement_range_km <= 0:
            continue
        rings.append(
            ThreatRing(
                threat_id=threat.threat_id,
                lat=threat.lat,
                lon=threat.lon,
                range_km=threat.engagement_range_km,
                pk=min(PK_CAP, threat.pk_base * weight),
                confidence=threat.confidence,
                min_alt_ft=threat.min_alt_ft,
                max_alt_ft=threat.max_alt_ft,
            )
        )
    return rings


def exposure(path: Sequence[Point], ring: ThreatRing, altitudes: Sequence[int] = ()) -> float:
    """Share of a full diameter crossing flown inside the ring, in [0, 1]. Zero when every altitude is outside its band."""
    if len(path) < 2 or ring.range_km <= 0:
        return 0.0
    if altitudes and not any(ring.min_alt_ft <= alt <= ring.max_alt_ft for alt in altitudes):
        return 0.0
    inside_km = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(path, path[1:]):
        length = haversine_km(lat1, lon1, lat2, lon2)
        steps = max(1, math.ceil(length / SAMPLE_KM))
        for step in range(steps):
            mid = (step + 0.5) / steps
            lat = lat1 + (lat2 - lat1) * mid
            lon = lon1 + (lon2 - lon1) * mid
            if haversine_km(lat, lon, ring.lat, ring.lon) < ring.range_km:
                inside_km += length / steps
    return min(1.0, inside_km / (2.0 * ring.range_km))


def loss_probability(path: Sequence[Point], rings: Sequence[ThreatRing], altitudes: Sequence[int] = ()) -> float:
    """Expected loss for one sortie: 1 - prod(1 - pk * confidence * exposure)."""
    survive = 1.0
    for ring in rings:
        share = exposure(path, ring, altitudes)
        if share > 0:
            survive *= 1.0 - min(PK_CAP, ring.pk * ring.confidence * share)
    return 1.0 - survive


def nearest_cell(cells, lat: float, lon: float, when: datetime):
    """Weather cell closest in time, then in distance. None when there are no cells."""
    if not cells:
        return None
    moment = as_utc(when)
    return min(
        cells,
        key=lambda cell: (abs((as_utc(cell.time) - moment).total_seconds()), haversine_km(lat, lon, cell.lat, cell.lon)),
    )
