"""Mission generator.

Arrival times follow the conditional law of a non-homogeneous Poisson process.
Given the mission count, times are i.i.d. draws from f(t) proportional to 1 + 2t/T,
so the rate rises across the horizon.
"""

from datetime import datetime, timedelta, timezone
from typing import cast

import numpy as np
from faker import Faker
from shapely.geometry import Polygon

from garuda.config import ScenarioConfig
from garuda.generators.targets import build_targets
from garuda.generators.threats import build_threats, suppress_sead_site
from garuda.generators.weapons import build_weapons
from garuda.generators.weather import build_weather
from garuda.schemas.mission import GeneratedScenario, Mission, MissionEffect, MissionType, Target

PRIORITY_WEIGHTS: dict[int, float] = {1: 0.10, 2: 0.20, 3: 0.35, 4: 0.25, 5: 0.10}
PRIORITY_VALUE: dict[int, float] = {1: 100, 2: 60, 3: 35, 4: 20, 5: 10}
PRIORITY_MIN_PK: dict[int, float] = {1: 0.80, 2: 0.70, 3: 0.55, 4: 0.40, 5: 0.30}

TYPE_WEIGHTS: dict[str, float] = {
    "DCA": 0.125,
    "CAP": 0.125,
    "STRIKE": 0.20,
    "CAS": 0.15,
    "ISR": 0.15,
    "SEAD": 0.10,
    "AAR": 0.10,
    "AIRLIFT": 0.05,
}

TYPE_EFFECT: dict[str, MissionEffect] = {
    "DCA": "DENY",
    "CAP": "DENY",
    "STRIKE": "DESTROY",
    "SEAD": "SUPPRESS",
    "CAS": "DESTROY",
    "ISR": "OBSERVE",
    "AAR": "OBSERVE",
    "AIRLIFT": "OBSERVE",
}

TYPE_CAPABILITIES: dict[str, list[str]] = {
    "DCA": ["BVR"],
    "CAP": ["BVR"],
    "STRIKE": ["AG_PGM"],
    "SEAD": ["AG_ARM"],
    "CAS": ["AG_PGM"],
    "ISR": ["ISR_SENSOR"],
    "AAR": ["AAR_OFFLOAD"],
    "AIRLIFT": ["AIRLIFT"],
}

TYPE_PACKAGE: dict[str, tuple[int, int]] = {
    "DCA": (2, 4),
    "CAP": (2, 4),
    "STRIKE": (2, 4),
    "SEAD": (2, 2),
    "CAS": (2, 4),
    "ISR": (1, 2),
    "AAR": (1, 1),
    "AIRLIFT": (1, 2),
}

TARGET_POLICY = {
    "DCA": "required",
    "CAP": "none",
    "STRIKE": "required",
    "SEAD": "required",
    "CAS": "required",
    "ISR": "optional",
    "AAR": "none",
    "AIRLIFT": "required",
}


def build_scenario(config: ScenarioConfig, seed: int) -> GeneratedScenario:
    rng = np.random.default_rng(seed)
    weapons, stocks, load_configs, compatibilities = build_weapons(config, seed)
    threats, tracks, clusters = build_threats(config, rng)
    targets = build_targets(config, rng, clusters)
    missions = _build_missions(config, seed, rng, targets)
    suppress_sead_site(threats, missions, targets)
    cells, observations = build_weather(config, rng)
    return GeneratedScenario(
        seed=seed,
        targets=targets,
        missions=missions,
        threats=threats,
        tracks=tracks,
        weapons=weapons,
        stocks=stocks,
        load_configs=load_configs,
        compatibilities=compatibilities,
        cells=cells,
        observations=observations,
    )


def _build_missions(
    config: ScenarioConfig,
    seed: int,
    rng: np.random.Generator,
    targets: list[Target],
) -> list[Mission]:
    if not targets:
        raise ValueError("missions require at least one target")

    count = int(rng.integers(40, 61))
    types = _allocate_labels(count, TYPE_WEIGHTS, rng)
    priority_labels = _allocate_labels(
        count,
        {str(priority): weight for priority, weight in PRIORITY_WEIGHTS.items()},
        rng,
    )
    priorities = [int(label) for label in priority_labels]
    latest_start = float(config.horizon_hours - 6)
    arrivals = _sample_arrival_hours(rng, count, latest_start)
    names = _requester_names(count, seed)
    start = _as_utc(config.start)
    horizon_end = start + timedelta(hours=config.horizon_hours)

    missions: list[Mission] = []
    for index in range(count):
        mission_type = _as_mission_type(types[index])
        priority = priorities[index]
        window_hours = float(rng.uniform(2.0, 6.0))
        window_start = start + timedelta(hours=float(arrivals[index]))
        window_end = window_start + timedelta(hours=window_hours)
        duration_min = int(rng.integers(30, int(window_hours * 60) + 1))
        missions.append(
            Mission(
                mission_id=f"M{index + 1:04d}",
                type=mission_type,
                priority=priority,
                value=PRIORITY_VALUE[priority],
                requested_by=names[index],
                window_start=window_start,
                window_end=window_end,
                duration_min=duration_min,
                target_id=_pick_target(mission_type, targets, rng),
                area_wkt=_area_wkt(config, rng) if mission_type in {"CAP", "ISR"} else None,
                required_capabilities=list(TYPE_CAPABILITIES[mission_type]),
                min_aircraft=TYPE_PACKAGE[mission_type][0],
                max_aircraft=TYPE_PACKAGE[mission_type][1],
                required_effect=TYPE_EFFECT[mission_type],
                min_pk=PRIORITY_MIN_PK[priority],
                status="REQUESTED",
            )
        )

    _inject_p1_requests(missions, config, targets, start)
    _link_sead_strike(missions, start, horizon_end)
    return [Mission.model_validate(mission.model_dump()) for mission in missions]


def _inject_p1_requests(
    missions: list[Mission],
    config: ScenarioConfig,
    targets: list[Target],
    start: datetime,
) -> None:
    used: set[str] = set()
    for event in config.events:
        if event.type != "p1_request":
            continue
        moment = start + timedelta(hours=event.t)
        ranked = sorted(
            missions,
            key=lambda mission: (abs((mission.window_start - moment).total_seconds()), mission.mission_id),
        )
        chosen = next(mission for mission in ranked if mission.mission_id not in used)
        used.add(chosen.mission_id)
        length = chosen.window_end - chosen.window_start
        chosen.type = "CAS"
        chosen.priority = 1
        chosen.value = PRIORITY_VALUE[1]
        chosen.min_pk = PRIORITY_MIN_PK[1]
        chosen.required_effect = TYPE_EFFECT["CAS"]
        chosen.required_capabilities = list(TYPE_CAPABILITIES["CAS"])
        chosen.min_aircraft = TYPE_PACKAGE["CAS"][0]
        chosen.max_aircraft = TYPE_PACKAGE["CAS"][1]
        chosen.target_id = targets[0].target_id
        chosen.area_wkt = None
        chosen.dependencies = []
        chosen.status = "REQUESTED"
        chosen.window_start = moment
        chosen.window_end = moment + length
        chosen.duration_min = max(30, int(length.total_seconds() // 60))


def _link_sead_strike(missions: list[Mission], start: datetime, horizon_end: datetime) -> None:
    seads = sorted(
        (mission for mission in missions if mission.type == "SEAD"),
        key=lambda mission: (mission.window_start, mission.mission_id),
    )
    strikes = sorted(
        (mission for mission in missions if mission.type == "STRIKE"),
        key=lambda mission: (mission.window_start, mission.mission_id),
    )
    for sead, strike in zip(seads, strikes, strict=False):
        length = strike.window_end - strike.window_start
        strike.window_start = sead.window_start + timedelta(minutes=30)
        strike.window_end = strike.window_start + length
        if strike.window_end > horizon_end:
            shift = strike.window_end - horizon_end
            sead.window_start -= shift
            sead.window_end -= shift
            strike.window_start -= shift
            strike.window_end -= shift
        if sead.window_start < start:
            raise RuntimeError(f"SEAD {sead.mission_id} was shifted before the scenario start")
        strike.dependencies = [sead.mission_id]
        _clamp_duration(strike)
        _clamp_duration(sead)


def _clamp_duration(mission: Mission) -> None:
    window_min = int((mission.window_end - mission.window_start).total_seconds() // 60)
    mission.duration_min = min(mission.duration_min, window_min)


def _pick_target(mission_type: str, targets: list[Target], rng: np.random.Generator) -> str | None:
    policy = TARGET_POLICY[mission_type]
    if policy == "none":
        return None
    if policy == "optional" and float(rng.random()) >= 0.5:
        return None
    return targets[int(rng.integers(0, len(targets)))].target_id


def _area_wkt(config: ScenarioConfig, rng: np.random.Generator) -> str:
    lat = float(rng.uniform(config.bbox.lat_min + 0.5, config.bbox.lat_max - 0.5))
    lon = float(rng.uniform(config.bbox.lon_min + 0.5, config.bbox.lon_max - 0.5))
    dlat = float(rng.uniform(0.2, 0.5))
    dlon = float(rng.uniform(0.2, 0.5))
    polygon = Polygon(
        [
            (lon - dlon, lat - dlat),
            (lon + dlon, lat - dlat),
            (lon + dlon, lat + dlat),
            (lon - dlon, lat + dlat),
            (lon - dlon, lat - dlat),
        ]
    )
    return polygon.wkt


def _sample_arrival_hours(rng: np.random.Generator, count: int, horizon_hours: float) -> np.ndarray:
    draw = rng.random(count)
    unit = (-1.0 + np.sqrt(1.0 + 8.0 * draw)) / 2.0
    return np.sort(unit * horizon_hours)


def _allocate_labels(count: int, weights: dict[str, float], rng: np.random.Generator) -> list[str]:
    counts = _allocate_counts(count, weights)
    labels: list[str] = []
    for name, copies in counts.items():
        labels.extend([name] * copies)
    order = rng.permutation(len(labels))
    return [labels[int(index)] for index in order]


def _allocate_counts(count: int, weights: dict[str, float]) -> dict[str, int]:
    names = list(weights)
    basis = 10_000
    points = [int(round(weights[name] * basis)) for name in names]
    points[-1] += basis - sum(points)
    raw = [count * point for point in points]
    counts = [value // basis for value in raw]
    remainder = count - sum(counts)
    ranked = sorted(range(len(names)), key=lambda index: (raw[index] % basis, -index), reverse=True)
    for index in range(remainder):
        counts[ranked[index]] += 1
    if sum(counts) != count:
        raise RuntimeError("allocation did not consume the requested count")
    return dict(zip(names, counts, strict=True))


def _requester_names(count: int, seed: int) -> list[str]:
    faker = Faker()
    faker.seed_instance(seed)
    return [faker.name() for _ in range(count)]


def _as_mission_type(label: str) -> MissionType:
    if label not in TYPE_EFFECT:
        raise ValueError(f"unknown mission type {label}")
    return cast(MissionType, label)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
