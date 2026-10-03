import numpy as np

from garuda.config import ScenarioConfig
from garuda.generators.threats import ThreatCluster
from garuda.schemas.mission import Target

TARGET_CLASSES = ("RUNWAY", "RADAR", "BRIDGE", "ARMOUR", "C2", "FUEL_DEPOT")


def build_targets(
    config: ScenarioConfig,
    rng: np.random.Generator,
    clusters: list[ThreatCluster],
) -> list[Target]:
    if not clusters:
        raise ValueError("targets require threat clusters")

    count = int(rng.integers(8, 16))
    targets: list[Target] = []
    for index in range(count):
        cluster = clusters[index % len(clusters)]
        threat_ids = list(cluster.threat_ids)
        if not threat_ids:
            raise ValueError(f"cluster {cluster.cluster_id} has no threats")
        lat = _clip(
            float(cluster.lat + rng.normal(0, 0.15)),
            config.bbox.lat_min,
            config.bbox.lat_max,
        )
        lon = _clip(
            float(cluster.lon + rng.normal(0, 0.15)),
            config.bbox.lon_min,
            config.bbox.lon_max,
        )
        defended_count = int(rng.integers(1, len(threat_ids) + 1))
        targets.append(
            Target(
                target_id=f"T{index + 1:04d}",
                target_class=TARGET_CLASSES[index % len(TARGET_CLASSES)],
                lon=lon,
                lat=lat,
                value=round(float(rng.uniform(20, 100)), 2),
                hardness=round(float(rng.uniform(0.1, 0.95)), 2),
                defended_by=threat_ids[:defended_count],
            )
        )
    return targets


def _clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))
