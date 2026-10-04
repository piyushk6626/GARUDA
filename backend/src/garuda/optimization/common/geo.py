"""Geometry primitives for optimisation tools: great-circle distance, travel time, threat-ring tests."""

import math

EARTH_RADIUS_KM = 6371.0
KM_PER_NM = 1.852


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    chord = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(chord)))


def travel_time_min(distance_km: float, speed_kts: float) -> float:
    return distance_km / (speed_kts * KM_PER_NM) * 60.0


def _local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (lon - lon0) * math.cos(math.radians(lat0)) * 111.32, (lat - lat0) * 110.57


def segment_hits_circle(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    radius_km: float,
) -> bool:
    """True when the (lat, lon) segment passes within `radius_km` of `center` (local planar approximation)."""
    ax, ay = _local_xy(start[0], start[1], center[0], center[1])
    bx, by = _local_xy(end[0], end[1], center[0], center[1])
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    fraction = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    return math.hypot(ax + fraction * dx, ay + fraction * dy) < radius_km
