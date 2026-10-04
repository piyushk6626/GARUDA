import os

from garuda.config import BoundingBox
from garuda.generators.gis import build_terrain
from garuda.schemas.mission import GeneratedScenario
from garuda.storage.gis_files import gis_root, write_terrain_files
from garuda.storage.kafka_publish import publish_scenario
from garuda.storage.minio import publish_raw, publish_terrain
from garuda.storage.neo4j_sync import sync_neo4j
from garuda.storage.postgres import seed_postgres
from garuda.storage.redis_seed import seed_redis

SCENARIO_LISTS = (
    "targets", "missions", "threats", "tracks", "weapons", "stocks", "load_configs",
    "compatibilities", "cells", "observations", "bases", "features", "borders", "distances",
    "aircraft_types", "squadrons", "aircraft", "aircraft_history", "crew", "crew_duty",
    "zones", "adsb_tracks",
)


def postgres_dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://garuda:garuda@localhost:5433/garuda")


def neo4j_settings() -> tuple[str, str, str]:
    return (
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", "garuda-dev"),
    )


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def kafka_bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092")


def scenario_counts(scenario: GeneratedScenario) -> dict[str, int]:
    counts = {name: len(getattr(scenario, name)) for name in SCENARIO_LISTS}
    counts["terrain_cells"] = scenario.terrain.rows * scenario.terrain.cols
    return counts


async def write_all(scenario: GeneratedScenario) -> dict[str, int]:
    await seed_postgres(postgres_dsn(), scenario)
    await sync_neo4j(*neo4j_settings(), scenario)
    pending, active, stock_keys = await seed_redis(redis_url(), scenario)
    events = await publish_scenario(kafka_bootstrap(), scenario)
    terrain = scenario.terrain
    bbox = BoundingBox(
        lat_min=terrain.lat_min,
        lat_max=terrain.lat_max,
        lon_min=terrain.lon_min,
        lon_max=terrain.lon_max,
    )
    root = gis_root()
    write_terrain_files(build_terrain(bbox, scenario.seed), root)
    objects = publish_terrain(root) + publish_raw(scenario, bbox)
    return {
        **scenario_counts(scenario),
        "pending": pending,
        "active_threats": active,
        "stock_keys": stock_keys,
        "events": events,
        "minio_objects": objects,
    }
