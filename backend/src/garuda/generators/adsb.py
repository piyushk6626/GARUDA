"""Recorded OpenSky sample plus seeded background tracks inside the theatre."""

import json
from datetime import datetime, timedelta, timezone
from math import cos, radians, sin
from pathlib import Path

import numpy as np

from garuda.config import ScenarioConfig
from garuda.ingest.adsb import normalize_opensky_response
from garuda.schemas.adsb import AdsbTrack

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "adsb" / "opensky_sample.json"
FIXTURE_SOURCE = "opensky-fixture"
SYNTHETIC_SOURCE = "synthetic"
BACKGROUND_COUNT = 12
BACKGROUND_STREAM = 51


def build_adsb(config: ScenarioConfig, seed: int, fixture_path: Path | None = None) -> list[AdsbTrack]:
    start = _as_utc(config.start)
    recorded = _recorded(fixture_path or FIXTURE_PATH, start)
    background = _background(config, seed, start)
    tracks = [*recorded, *background]
    tracks.sort(key=lambda track: (track.time, track.source_id, track.icao24))
    return tracks


def _recorded(path: Path, start: datetime) -> list[AdsbTrack]:
    document = json.loads(path.read_text())
    original = document.get("time")
    if not isinstance(original, (int, float)):
        raise ValueError(f"OpenSky fixture is missing a response time: {path}")
    delta = start - datetime.fromtimestamp(float(original), tz=timezone.utc)
    shifted = _shift(document, delta)
    result = normalize_opensky_response(shifted, source_id=FIXTURE_SOURCE)
    if result.dropped:
        raise ValueError(f"recorded OpenSky fixture dropped {result.dropped} rows")
    return list(result.observations)


def _shift(document: dict, delta: timedelta) -> dict:
    shifted = json.loads(json.dumps(document))
    seconds = delta.total_seconds()
    if isinstance(shifted.get("time"), (int, float)):
        shifted["time"] = float(shifted["time"]) + seconds
    for row in shifted.get("states") or []:
        if not isinstance(row, list):
            continue
        for index in (3, 4):
            if len(row) > index and isinstance(row[index], (int, float)):
                row[index] = float(row[index]) + seconds
    return shifted


def _background(config: ScenarioConfig, seed: int, start: datetime) -> list[AdsbTrack]:
    rng = np.random.default_rng(np.random.SeedSequence([seed, BACKGROUND_STREAM]))
    bbox = config.bbox
    tracks: list[AdsbTrack] = []
    for index in range(BACKGROUND_COUNT):
        icao = f"{0xB00000 + index:06x}"
        callsign = f"SYN{index:03d}"
        lat = float(rng.uniform(bbox.lat_min + 0.2, bbox.lat_max - 0.2))
        lon = float(rng.uniform(bbox.lon_min + 0.2, bbox.lon_max - 0.2))
        heading = float(rng.uniform(0.0, 359.0))
        speed = float(rng.uniform(120.0, 260.0))
        altitude = float(rng.uniform(3000.0, 11000.0))
        on_ground = index == BACKGROUND_COUNT - 1
        for step in range(3):
            moment = start - timedelta(seconds=60 * (2 - step))
            offset_m = speed * 60.0 * step
            north = offset_m * cos(radians(heading)) / 111_000.0
            east = offset_m * sin(radians(heading)) / 111_000.0
            point_lat = min(bbox.lat_max - 0.05, max(bbox.lat_min + 0.05, lat + north))
            point_lon = min(bbox.lon_max - 0.05, max(bbox.lon_min + 0.05, lon + east))
            tracks.append(
                AdsbTrack(
                    time=moment,
                    source_id=SYNTHETIC_SOURCE,
                    icao24=icao,
                    callsign=callsign,
                    origin_country="FABRICATED",
                    last_contact=moment,
                    lat=point_lat,
                    lon=point_lon,
                    alt_m=0.0 if on_ground else altitude,
                    geo_alt_m=0.0 if on_ground else altitude + 40.0,
                    speed_ms=0.0 if on_ground else speed,
                    heading=heading,
                    vrate=0.0,
                    on_ground=on_ground,
                    position_source=0,
                )
            )
    return tracks


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
