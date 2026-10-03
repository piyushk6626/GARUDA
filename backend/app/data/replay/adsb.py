"""Deterministic, explicitly fabricated OpenSky replay support."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from ...config import BoundingBox
from ...schemas.adsb import EventEnvelope, normalize_opensky_response


@dataclass(frozen=True)
class FrozenClock:
    now_value: datetime
    def now(self) -> datetime:
        return self.now_value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReplayPlan:
    replay_id: str
    source_id: str
    fixture_checksum: str
    events: tuple[EventEnvelope, ...]
    label: str = "FABRICATED OFFLINE REPLAY — NOT LIVE AIR TRAFFIC"


def _shift_document(document: dict, start: datetime) -> dict:
    shifted = copy.deepcopy(document)
    original_time = int(document.get("time") or start.timestamp())
    delta = int(start.timestamp()) - original_time
    shifted["time"] = original_time + delta
    for row in shifted.get("states") or []:
        if isinstance(row, list):
            for index in (3, 4):
                if len(row) > index and isinstance(row[index], (int, float)):
                    row[index] += delta
    return shifted


def _background(document: dict, seed: int, bbox: BoundingBox, count: int) -> None:
    rng = np.random.default_rng(seed)
    timestamp = int(document["time"])
    states = document.setdefault("states", [])
    for number in range(count):
        icao = f"{int(rng.integers(0, 16**6)):06x}"
        states.append([icao, f"SIM{number:03d}", "FABRICATED", timestamp,
                       timestamp, float(rng.uniform(bbox.lomin, bbox.lomax)),
                       float(rng.uniform(bbox.lamin, bbox.lamax)),
                       float(rng.uniform(3000, 12000)), False,
                       float(rng.uniform(120, 280)), float(rng.uniform(0, 359.99)),
                       float(rng.uniform(-5, 5)), None, None, None, False, 0])


def build_replay(fixture: Path | str, *, replay_id: str, start: datetime, seed: int,
                 bbox: BoundingBox, background_count: int = 0) -> ReplayPlan:
    """Same inputs produce byte-for-byte equivalent event envelopes."""
    content = Path(fixture).read_bytes()
    document = _shift_document(json.loads(content), start.astimezone(timezone.utc))
    _background(document, seed, bbox, background_count)
    source_id = f"replay:{replay_id}"
    result = normalize_opensky_response(document, source_id=source_id,
        ingested_at=start.astimezone(timezone.utc), replay_id=replay_id)
    return ReplayPlan(replay_id=replay_id, source_id=source_id,
                      fixture_checksum=hashlib.sha256(content).hexdigest(),
                      events=tuple(item.to_event() for item in result.observations))


def replay_events(plan: ReplayPlan) -> Iterable[EventEnvelope]:
    """A deliberately simple deterministic source for CLI/worker injection."""
    return plan.events
