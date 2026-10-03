import numpy as np

from garuda.config import ScenarioConfig
from garuda.schemas.mission import Target

TARGET_CLASSES = ("RUNWAY", "RADAR", "BRIDGE", "ARMOUR", "C2", "FUEL_DEPOT")


def build_targets(config: ScenarioConfig, rng: np.random.Generator) -> list[Target]:
    count = int(rng.integers(8, 16))
    cluster_count = 3
    centers_lat = rng.uniform(24.0, 31.5, size=cluster_count)
    centers_lon = rng.uniform(69.0, 72.0, size=cluster_count)
    threat_ids = [
        [f"TH-{cluster:02d}-{index:02d}" for index in range(1, 3)]
        for cluster in range(cluster_count)
    ]

    targets: list[Target] = []
    for index in range(count):
        cluster = index % cluster_count
        lat = _clip(
            float(centers_lat[cluster] + rng.normal(0, 0.15)),
            config.bbox.lat_min,
            config.bbox.lat_max,
        )
        lon = _clip(
            float(centers_lon[cluster] + rng.normal(0, 0.15)),
            config.bbox.lon_min,
            config.bbox.lon_max,
        )
        cluster_threats = threat_ids[cluster]
        defended_count = int(rng.integers(1, len(cluster_threats) + 1))
        targets.append(
            Target(
                target_id=f"T{index + 1:04d}",
                target_class=TARGET_CLASSES[index % len(TARGET_CLASSES)],
                lon=lon,
                lat=lat,
                value=round(float(rng.uniform(20, 100)), 2),
                hardness=round(float(rng.uniform(0.1, 0.95)), 2),
                defended_by=cluster_threats[:defended_count],
            )
        )
    return targets


def _clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))
