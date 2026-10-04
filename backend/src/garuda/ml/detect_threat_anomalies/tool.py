import asyncio
import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from shapely.geometry import Point, Polygon

from garuda.common import as_utc
from garuda.contracts.geo import LatLon
from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.ml.detect_threat_anomalies.schemas import (
    AnomalyAlert,
    DetectThreatAnomaliesInput,
    DetectThreatAnomaliesOutput,
)

SPEC = ToolSpec(
    name="detect_threat_anomalies",
    description="Flag unusual threat or track activity.",
    input_model=DetectThreatAnomaliesInput,
    output_model=DetectThreatAnomaliesOutput,
    side_effects="none",
    timeout_s=20,
)

# Compatibility fixture for the existing graph skeleton; inference never uses it.
STUB_OUTPUT = DetectThreatAnomaliesOutput(
    alerts=[
        AnomalyAlert(
            id="A-001",
            type="new_cluster",
            location=LatLon(lat=27.1, lon=78.4),
            score=0.82,
            evidence=["isolation_forest"],
            suggested_threat_update="new_sam",
        )
    ],
    baseline_period="6h",
)


async def detect_threat_anomalies(
    payload: DetectThreatAnomaliesInput, ctx: ToolContext
) -> DetectThreatAnomaliesOutput:
    """Read-only ensemble over the snapshot's threat and airborne ADS-B history.

    Historical windows precede the requested lookback, so neither future data nor
    the activity being scored trains the baseline. Emitter events are not yet part
    of ScenarioSource; no synthetic emitter feed is invented here.
    """
    if payload.lookback_hours <= 0:
        raise ToolInputError("lookback_hours must be positive")
    area = _area(payload)
    # Shared run_cpu is still a skeleton. Fit and port reads stay off the event loop.
    return await asyncio.to_thread(_detect, payload, ctx, area)


_WINDOW_S = 1800
_CELL_DEGREES = 0.1
_EARTH_KM = 6371.0
_GROUND_TYPES = {"SAM_LONG", "SAM_MED", "SAM_SHORT", "RADAR_EW", "RADAR_GCI", "AAA"}
# Generic envelopes, not platform-specific performance claims.
_SPEED_KTS = {"civil": 700.0, "ENEMY_CAP": 900.0, "unknown": 900.0}
_THRESHOLDS = {"low": (0.75, 0.99), "medium": (0.65, 0.97), "high": (0.55, 0.93)}


@dataclass(frozen=True)
class _Observation:
    time: datetime
    entity: str
    kind: str
    lat: float
    lon: float
    speed: float | None
    heading: float | None


@dataclass
class _Candidate:
    time: datetime
    type: str
    lat: float
    lon: float
    key: str
    update: str
    scores: dict[str, float] = field(default_factory=dict)
    details: list[str] = field(default_factory=list)
    family: str = "ground"


def _area(payload):
    if payload.area is None:
        return None
    ring = payload.area.ring
    if any(
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not -90 <= lat <= 90
        or not -180 <= lon <= 180
        for lat, lon in ring
    ):
        raise ToolInputError("area must contain finite (lat, lon) coordinates in range")
    # Unwrap across the antimeridian, preserving the project's (lat, lon) order.
    origin = ring[0][1]
    shape = Polygon([(_unwrap(lon, origin), lat) for lat, lon in ring])
    if not shape.is_valid or shape.is_empty or shape.area == 0:
        raise ToolInputError("area must be a valid polygon with nonzero area")
    return shape, origin


def _unwrap(lon, origin):
    return origin + (lon - origin + 180) % 360 - 180


def _inside(area, lat, lon):
    return area is None or area[0].covers(Point(_unwrap(lon, area[1]), lat))


def _distance(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * _EARTH_KM * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def _location(rows):
    """Use an observed point near the centre, including in concave areas."""
    origin = rows[0].lon
    lat = float(np.mean([r.lat for r in rows]))
    lon = float(np.mean([_unwrap(r.lon, origin) for r in rows]))
    closest = min(
        rows, key=lambda r: (_distance(lat, lon, r.lat, r.lon), r.entity, r.time)
    )
    return closest.lat, closest.lon


def _observations(snapshot, now, history_start):
    threats = {t.threat_id: t for t in snapshot.threats if as_utc(t.first_seen) <= now}
    all_ids = {t.threat_id for t in snapshot.threats}
    rows = []
    for track in snapshot.tracks:
        threat = threats.get(track.threat_id)
        if threat is not None and (
            threat.status == "DESTROYED"
            or as_utc(threat.first_seen) > as_utc(track.time)
        ):
            continue
        # A track linked to a future site must not leak into present inference.
        if threat is None and track.threat_id in all_ids:
            continue
        rows.append(
            _Observation(
                as_utc(track.time),
                f"threat:{track.threat_id}",
                threat.type if threat else "unknown",
                track.lat,
                track.lon,
                track.speed_kts,
                track.heading,
            )
        )
    for track in snapshot.adsb_tracks:
        if not track.on_ground:
            rows.append(
                _Observation(
                    as_utc(track.time),
                    f"civil:{track.source_id}:{track.icao24}",
                    "civil",
                    track.lat,
                    track.lon,
                    None if track.speed_ms is None else track.speed_ms / 0.514444,
                    track.heading,
                )
            )
    # Duplicate telemetry must not manufacture density, persistence, or clusters.
    unique = {}
    for row in sorted(
        rows,
        key=lambda r: (r.time, r.entity, r.lat, r.lon, r.speed or 0, r.heading or 0),
    ):
        if not history_start <= row.time <= now:
            continue
        values = [row.lat, row.lon] + [
            v for v in (row.speed, row.heading) if v is not None
        ]
        if (
            not all(math.isfinite(v) for v in values)
            or not -90 <= row.lat <= 90
            or not -180 <= row.lon <= 180
            or (row.speed is not None and row.speed < 0)
        ):
            raise ToolInputError(f"invalid track observation for {row.entity}")
        unique.setdefault((row.entity, row.time), row)
    return sorted(unique.values(), key=lambda r: (r.time, r.entity)), threats


def _detect(payload, ctx, area):
    now = as_utc(ctx.scenario_time)
    try:
        cutoff = now - timedelta(hours=payload.lookback_hours)
        history_start = cutoff - timedelta(days=7)
    except OverflowError as error:
        raise ToolInputError(
            "lookback_hours is outside the supported datetime range"
        ) from error
    snapshot = ctx.scenario.snapshot()
    rows, threats = _observations(snapshot, now, history_start)
    scoped = [r for r in rows if _inside(area, r.lat, r.lon)]
    baseline = [r for r in scoped if r.time <= cutoff]
    recent = [r for r in scoped if r.time > cutoff]
    period = (
        f"{baseline[0].time.isoformat()}/{cutoff.isoformat()}"
        if baseline
        else "unavailable: no observations before lookback"
    )
    if not recent:
        return DetectThreatAnomaliesOutput(alerts=[], baseline_period=period)
    candidates = _window_candidates(
        baseline, recent, cutoff, now, snapshot.borders, ctx.seed
    )
    candidates.extend(_clusters(recent, rows, threats, cutoff))
    candidates.extend(_kinematics(rows, cutoff, area))
    return DetectThreatAnomaliesOutput(
        alerts=_fuse(candidates, payload.sensitivity), baseline_period=period
    )


def _bucket(moment):
    # Right-closed windows: a sample exactly at the clock belongs to the window ending then.
    return math.ceil(moment.timestamp() / _WINDOW_S) - 1


def _cell(row):
    family = (
        "civil"
        if row.kind == "civil"
        else "cap" if row.kind == "ENEMY_CAP" else "ground"
    )
    return (
        family,
        math.floor(row.lat / _CELL_DEGREES),
        math.floor(row.lon / _CELL_DEGREES),
    )


def _strength(deviation):
    return float(-math.expm1(-max(0.0, deviation) / 3))


def _window_candidates(baseline, recent, cutoff, now, borders, seed):
    grouped = defaultdict(list)
    for row in baseline + recent:
        grouped[(_cell(row), _bucket(row.time))].append(row)
    # Only complete historical windows provide comparable count baselines.
    start = math.ceil(baseline[0].time.timestamp() / _WINDOW_S) if baseline else 0
    end = math.floor(cutoff.timestamp() / _WINDOW_S)
    if not baseline or end - start < 4:
        return []
    cells = sorted({_cell(r) for r in baseline + recent})
    history = {}
    for cell in cells:
        history[cell] = np.asarray(
            [
                len({r.entity for r in grouped.get((cell, i), [])})
                for i in range(start, end)
            ],
            dtype=float,
        )
    # Parse border lines once. Degree-space nearest points are used only as an
    # approximate feature; all alert distances use great-circle kilometres.
    from shapely import wkt
    from shapely.ops import nearest_points, unary_union

    border = unary_union([wkt.loads(b.wkt) for b in borders]) if borders else None

    def vector(rows, cell, count):
        speeds = [r.speed for r in rows if r.speed is not None]
        angles = [math.radians(r.heading) for r in rows if r.heading is not None]
        variance = 1 - abs(np.mean(np.exp(1j * np.asarray(angles)))) if angles else 0.0
        distance = 0.0
        if border is not None and not border.is_empty and rows:
            lat, lon = _location(rows)
            point = nearest_points(Point(lon, lat), border)[1]
            distance = _distance(lat, lon, point.y, point.x)
        mean = float(np.mean(history[cell]))
        sigma = max(1.0, float(np.std(history[cell])), math.sqrt(mean))
        return [
            count,
            float(np.mean(speeds)) if speeds else 0.0,
            float(variance),
            distance,
            (count - mean) / sigma,
        ]

    # Fit only observed historical windows: empty cells supply count statistics,
    # but fabricated zero-speed vectors would distort the isolation forest.
    training = [
        vector(grouped[(cell, i)], cell, history[cell][i - start])
        for cell in cells
        for i in range(start, end)
        if (cell, i) in grouped
    ]
    forest = None
    if len(training) >= 16:
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError as error:
            raise RuntimeError(
                "detect_threat_anomalies requires the existing ML extra: pip install 'garuda[ml]'"
            ) from error
        forest = IsolationForest(contamination=0.02, random_state=seed, n_jobs=1)
        forest.fit(training)
        raw = -forest.score_samples(training)
        middle, upper = float(np.median(raw)), float(np.quantile(raw, 0.98))
    candidates = []
    current_groups = defaultdict(list)
    for row in recent:
        current_groups[(_cell(row), _bucket(row.time))].append(row)
    cusum = defaultdict(float)
    # Iterate intervening empty windows as well, so CUSUM decays during quiet periods.
    for cell in sorted({key[0] for key in current_groups}):
        counts = history[cell]
        for bucket in range(
            _bucket(cutoff + timedelta(microseconds=1)), _bucket(now) + 1
        ):
            window_start = datetime.fromtimestamp(bucket * _WINDOW_S, timezone.utc)
            window_end = window_start + timedelta(seconds=_WINDOW_S)
            exposure = (
                min(window_end, now) - max(window_start, cutoff)
            ).total_seconds() / _WINDOW_S
            if exposure <= 0:
                continue
            # Hour-of-day baseline when >=3 historical days provide observations.
            matched = [
                v
                for i, v in enumerate(counts, start=start)
                if (i // 2) % 24 == (bucket // 2) % 24
            ]
            reference = np.asarray(matched) if len(matched) >= 6 else counts
            mean = float(np.mean(reference)) * exposure
            sigma = max(
                1.0, float(np.std(reference)) * math.sqrt(exposure), math.sqrt(mean)
            )
            current = current_groups.get((cell, bucket), [])
            count = len({r.entity for r in current})
            z = (count - mean) / sigma
            cusum[cell] = max(0.0, cusum[cell] + z - 0.5)
            if not current:
                continue
            lat, lon = _location(current)
            candidate = _Candidate(
                max(r.time for r in current),
                "cap_density" if cell[0] == "cap" else "activity_rate_change",
                lat,
                lon,
                repr((cell, bucket)),
                "review_activity",
                family=cell[0],
            )
            candidate.scores = {
                "z_score": _strength(z),
                "cusum": _strength(cusum[cell] / 2),
            }
            candidate.details.append(
                f"window={window_start.isoformat()}; unique_tracks={count}; baseline_mean={mean:.3f}; z={z:.3f}"
            )
            # Short partial windows cannot be extrapolated into a forest anomaly.
            if forest is not None and exposure >= 0.5:
                raw = float(
                    -forest.score_samples([vector(current, cell, count / exposure)])[0]
                )
                score = (
                    0.0
                    if upper - middle < 1e-8
                    else (raw - middle) / (upper - middle) * 0.8
                )
                candidate.scores["isolation_forest"] = float(np.clip(score, 0, 1))
            candidates.append(candidate)
    return candidates


def _clusters(recent, all_rows, threats, cutoff):
    """New ground concentrations, excluding known air traffic and ordinary mobile tracks."""
    known = [
        t
        for t in threats.values()
        if as_utc(t.first_seen) <= cutoff
        and t.type in _GROUND_TYPES
        and t.status != "DESTROYED"
    ]
    # Mobile units' historical locations supplement their static site position.
    anchors = [(t.lat, t.lon) for t in known]
    anchors.extend(
        (r.lat, r.lon)
        for r in all_rows
        if r.time <= cutoff and (r.kind in _GROUND_TYPES or r.kind == "unknown")
    )
    grouped = defaultdict(list)
    for row in recent:
        if row.kind not in _GROUND_TYPES and row.kind != "unknown":
            continue
        threat = threats.get(row.entity.removeprefix("threat:"))
        if threat is not None and threat.mobile:
            continue
        if any(_distance(row.lat, row.lon, lat, lon) <= 5 for lat, lon in anchors):
            continue
        grouped[_bucket(row.time)].append(row)
    result = []
    if not any(len(rows) >= 3 for rows in grouped.values()):
        return result
    try:
        from sklearn.cluster import DBSCAN
    except ImportError as error:
        raise RuntimeError(
            "detect_threat_anomalies requires the existing ML extra: pip install 'garuda[ml]'"
        ) from error
    for bucket, rows in sorted(grouped.items()):
        if len(rows) < 3:
            continue
        coords = np.radians([(r.lat, r.lon) for r in rows])
        labels = DBSCAN(
            eps=5 / _EARTH_KM,
            min_samples=3,
            metric="haversine",
            algorithm="ball_tree",
            n_jobs=1,
        ).fit_predict(coords)
        for label in sorted(set(labels) - {-1}):
            cluster = [r for r, value in zip(rows, labels) if value == label]
            lat, lon = _location(cluster)
            # A spatial cluster is sufficient only with strong repeated evidence.
            score = 1 - math.exp(-len(cluster) / 3)
            result.append(
                _Candidate(
                    max(r.time for r in cluster),
                    "new_cluster",
                    lat,
                    lon,
                    repr((bucket, sorted({r.entity for r in cluster}))),
                    "new_sam",
                    {"new_cluster": score},
                    [
                        f"detections={len(cluster)}; >5km from known ground sites; analyst confirmation required"
                    ],
                )
            )
    return result


def _kinematics(rows, cutoff, area):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.entity].append(row)
    candidates = []
    for entity, history in sorted(grouped.items()):
        origin = history[0]
        # Constant-velocity Kalman state: east/north km, east/north km/h.
        cos_lat = max(1e-6, math.cos(math.radians(origin.lat)))
        state = None
        covariance = np.diag([1.0, 1.0, 100.0, 100.0])
        previous = None
        for row in history:
            limit = _SPEED_KTS.get(
                row.kind, 60.0 if row.kind in _GROUND_TYPES else 900.0
            )
            position = np.asarray(
                [
                    math.radians(_unwrap(row.lon, origin.lon) - origin.lon)
                    * _EARTH_KM
                    * cos_lat,
                    math.radians(row.lat - origin.lat) * _EARTH_KM,
                ]
            )
            scores = {}
            if row.speed is not None:
                scores["speed_envelope"] = _strength(9 * (row.speed / limit - 1))
            if previous is not None:
                dt = (row.time - previous.time).total_seconds() / 3600
                transition = np.eye(4)
                transition[0, 2] = transition[1, 3] = dt
                # Acceleration allowance absorbs normal turns and sparse reports.
                acceleration = limit * 1.852 * 4
                drive = np.asarray(
                    [[dt * dt / 2, 0], [0, dt * dt / 2], [dt, 0], [0, dt]]
                )
                predicted = transition @ state
                covariance = (
                    transition @ covariance @ transition.T
                    + drive @ drive.T * acceleration**2
                )
                innovation = position - predicted[:2]
                variance = covariance[:2, :2] + np.eye(2)
                mahalanobis = float(innovation @ np.linalg.solve(variance, innovation))
                scores["kalman_innovation"] = _strength(
                    math.sqrt(max(0, mahalanobis)) - 3
                )
                implied = (
                    _distance(previous.lat, previous.lon, row.lat, row.lon) / dt / 1.852
                )
                scores["displacement_speed"] = _strength(9 * (implied / limit - 1))
                gain = np.linalg.solve(variance, covariance[:2, :]).T
                state = predicted + gain @ innovation
                # Joseph form preserves positive semidefiniteness over long histories.
                residual = np.eye(4)
                residual[:, :2] -= gain
                covariance = residual @ covariance @ residual.T + gain @ gain.T
            else:
                angle = math.radians(row.heading or 0)
                speed = (row.speed or 0) * 1.852
                state = np.asarray(
                    [*position, speed * math.sin(angle), speed * math.cos(angle)]
                )
            if row.time > cutoff and _inside(area, row.lat, row.lon):
                candidates.append(
                    _Candidate(
                        row.time,
                        (
                            "civil_track_deviation"
                            if row.kind == "civil"
                            else (
                                "sam_relocation"
                                if row.kind.startswith("SAM_")
                                else "track_kinematic_anomaly"
                            )
                        ),
                        row.lat,
                        row.lon,
                        entity,
                        (
                            "review_civil_track"
                            if row.kind == "civil"
                            else "position_update"
                        ),
                        scores,
                        [
                            f"entity={entity}; time={row.time.isoformat()}; speed_kts={row.speed}; envelope_kts={limit}"
                        ],
                        family=(
                            "civil"
                            if row.kind == "civil"
                            else "cap" if row.kind == "ENEMY_CAP" else "ground"
                        ),
                    )
                )
            previous = row
    return candidates


def _fuse(candidates, sensitivity):
    agreement, high = _THRESHOLDS[sensitivity]
    windows = [
        c for c in candidates if c.type in {"activity_rate_change", "cap_density"}
    ]
    # Count/forest detectors can corroborate a cluster or a deviating track in
    # the same window. Keep original measurements separate until this stage.
    for candidate in candidates:
        if candidate.type in {"activity_rate_change", "cap_density"}:
            continue
        if max(candidate.scores.values(), default=0) < 0.5:
            continue
        for window in windows:
            if (
                window.family == candidate.family
                and _bucket(window.time) == _bucket(candidate.time)
                and _distance(window.lat, window.lon, candidate.lat, candidate.lon) <= 5
            ):
                for detector, score in window.scores.items():
                    candidate.scores[detector] = max(
                        score, candidate.scores.get(detector, 0)
                    )
                candidate.details.extend(window.details)
    accepted = []
    for candidate in candidates:
        votes = [name for name, score in candidate.scores.items() if score >= agreement]
        score = max(candidate.scores.values(), default=0.0)
        if len(votes) >= 2 or score > high:
            accepted.append((candidate, votes, score))
    accepted.sort(key=lambda item: (-item[2], item[0].time, item[0].type, item[0].key))
    selected = []
    for candidate, votes, score in accepted:
        # Dedupe each anomaly family within 10km / 1h, preserving distinct civil,
        # ground, and CAP findings and the strongest representative's location.
        match = next(
            (
                item
                for item in selected
                if item[0].type == candidate.type
                and abs((item[0].time - candidate.time).total_seconds()) <= 3600
                and _distance(item[0].lat, item[0].lon, candidate.lat, candidate.lon)
                <= 10
            ),
            None,
        )
        if match is not None:
            match[1].update(votes)
            for detail in candidate.details:
                if detail not in match[3] and len(match[3]) < 8:
                    match[3].append(detail)
        else:
            selected.append([candidate, set(votes), score, list(candidate.details)])
    alerts = []
    for candidate, votes, score, details in selected:
        identity = f"{candidate.type}|{candidate.key}|{candidate.time.isoformat()}|{candidate.lat:.6f}|{candidate.lon:.6f}"
        alerts.append(
            AnomalyAlert(
                id="A-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                type=candidate.type,
                location=LatLon(lat=candidate.lat, lon=candidate.lon),
                score=round(score, 6),
                evidence=sorted(votes) + details,
                suggested_threat_update=candidate.update,
            )
        )
    return alerts


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, detect_threat_anomalies)
