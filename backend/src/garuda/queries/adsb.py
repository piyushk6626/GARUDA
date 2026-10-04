"""Live-track, density, and conflict-candidate lookups over ADS-B rows.

Freshness is measured from position time against the caller's clock.
A synthetic or empty result is not evidence that the airspace is empty.
"""

from datetime import datetime, timedelta, timezone

from garuda.generators.gis import great_circle_km
from garuda.schemas.adsb import AdsbTrack

FRESH_SECONDS = 120
CONFLICT_KM = 10.0
CONFLICT_FT = 1000.0
CONFLICT_SECONDS = 300
_METRES_PER_FOOT = 0.3048
CELL_DEGREES = 0.05


def get_civil_traffic(
    tracks: list[AdsbTrack],
    *,
    as_of: datetime,
    lat_min: float,
    lon_min: float,
    lat_max: float,
    lon_max: float,
    limit: int = 200,
) -> dict[str, object]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    fresh = [
        track
        for track in latest_tracks(tracks)
        if _fresh(track, as_of) and lat_min <= track.lat <= lat_max and lon_min <= track.lon <= lon_max
    ]
    fresh.sort(key=lambda track: track.icao24)
    items = fresh[:limit]
    sources = sorted({track.source_id for track in items})
    return {
        "items": items,
        "data_as_of": as_of,
        "source": ",".join(sources) if sources else "none",
        "coverage": "synthetic_or_recorded_snapshot",
        "truncated": len(fresh) > limit,
    }


def latest_tracks(tracks: list[AdsbTrack]) -> list[AdsbTrack]:
    latest: dict[tuple[str, str], AdsbTrack] = {}
    for track in tracks:
        key = (track.source_id, track.icao24)
        current = latest.get(key)
        if current is None or track.time >= current.time:
            latest[key] = track
    return list(latest.values())


def density_cells(
    tracks: list[AdsbTrack],
    *,
    cell_degrees: float = CELL_DEGREES,
    exclude_ground: bool = True,
) -> list[dict[str, object]]:
    """Unique aircraft per cell per hour. Headings are not averaged."""
    if cell_degrees <= 0:
        raise ValueError("cell_degrees must be positive")
    occupied: set[tuple[str, str, int, int]] = set()
    for track in tracks:
        if exclude_ground and track.on_ground:
            continue
        hour = track.time.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        lat_cell = int(track.lat // cell_degrees)
        lon_cell = int(track.lon // cell_degrees)
        occupied.add((hour.strftime("%Y-%m-%dT%H:%M:%SZ"), track.icao24, lat_cell, lon_cell))
    counts: dict[tuple[str, int, int], int] = {}
    for hour, _icao, lat_cell, lon_cell in occupied:
        key = (hour, lat_cell, lon_cell)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "hour": hour,
            "lat_cell": lat_cell,
            "lon_cell": lon_cell,
            "cell_degrees": cell_degrees,
            "aircraft": count,
        }
        for (hour, lat_cell, lon_cell), count in sorted(counts.items())
    ]


def five_minute_summaries(tracks: list[AdsbTrack]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[AdsbTrack]] = {}
    for track in tracks:
        bucket = _five_minute_bucket(track.time)
        grouped.setdefault((bucket, track.source_id, track.icao24), []).append(track)
    summaries: list[dict[str, object]] = []
    for (bucket, source_id, icao24), rows in sorted(grouped.items()):
        altitudes = [row.alt_m for row in rows if row.alt_m is not None]
        speeds = [row.speed_ms for row in rows if row.speed_ms is not None]
        summaries.append(
            {
                "bucket": bucket,
                "source_id": source_id,
                "icao24": icao24,
                "samples": len(rows),
                "alt_min_m": min(altitudes) if altitudes else None,
                "alt_max_m": max(altitudes) if altitudes else None,
                "speed_ms_mean": sum(speeds) / len(speeds) if speeds else None,
            }
        )
    return summaries


def conflict_candidates(
    tracks: list[AdsbTrack],
    *,
    lat: float,
    lon: float,
    altitude_ft: float | None,
    at: datetime,
) -> list[dict[str, object]]:
    """10 km, 1000 ft, and ±5 min. Unknown altitude stays an unknown assessment."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    window = timedelta(seconds=CONFLICT_SECONDS)
    findings: list[dict[str, object]] = []
    for track in tracks:
        if abs(track.time - at) > window:
            continue
        distance_km = great_circle_km(lat, lon, track.lat, track.lon)
        if distance_km > CONFLICT_KM:
            continue
        assessment = _vertical(altitude_ft, track.alt_m)
        if assessment == "clear":
            continue
        findings.append(
            {
                "icao24": track.icao24,
                "source_id": track.source_id,
                "distance_km": distance_km,
                "assessment": assessment,
                "time": track.time,
            }
        )
    findings.sort(key=lambda item: (str(item["icao24"]), str(item["time"])))
    return findings


def _fresh(track: AdsbTrack, as_of: datetime) -> bool:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    age = (as_of - track.time).total_seconds()
    return 0 <= age <= FRESH_SECONDS


def _vertical(altitude_ft: float | None, alt_m: float | None) -> str:
    if altitude_ft is None or alt_m is None:
        return "unknown"
    altitude_m = altitude_ft * _METRES_PER_FOOT
    if abs(altitude_m - alt_m) <= CONFLICT_FT * _METRES_PER_FOOT:
        return "conflict"
    return "clear"


def _five_minute_bucket(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc)
    minute = utc.minute - utc.minute % 5
    bucket = utc.replace(minute=minute, second=0, microsecond=0)
    return bucket.strftime("%Y-%m-%dT%H:%M:%SZ")
