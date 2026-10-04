import os
from dataclasses import dataclass

from garuda.schemas.mission import GeneratedScenario
from garuda.storage.kafka_publish import publish_missions, publish_stocks, publish_threats, publish_weather
from garuda.storage.neo4j_sync import sync_neo4j
from garuda.storage.postgres import seed_postgres
from garuda.storage.redis_seed import seed_redis


@dataclass(frozen=True)
class SeedResult:
    targets: int
    missions: int
    pending: int
    events: int
    threats: int
    tracks: int
    active_threats: int
    weapons: int
    stocks: int
    load_configs: int
    compatibilities: int
    stock_keys: int
    cells: int
    observations: int


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


async def write_all(scenario: GeneratedScenario) -> SeedResult:
    await seed_postgres(
        postgres_dsn(),
        scenario.targets,
        scenario.missions,
        scenario.threats,
        scenario.tracks,
        scenario.weapons,
        scenario.stocks,
        scenario.load_configs,
        scenario.compatibilities,
        scenario.cells,
        scenario.observations,
    )
    uri, user, password = neo4j_settings()
    await sync_neo4j(
        uri,
        user,
        password,
        scenario.targets,
        scenario.missions,
        scenario.threats,
        scenario.weapons,
        scenario.stocks,
        scenario.cells,
    )
    pending, active, stock_keys = await seed_redis(
        redis_url(),
        scenario.missions,
        scenario.threats,
        scenario.stocks,
        scenario.cells,
        scenario.observations,
    )
    events = await publish_missions(kafka_bootstrap(), scenario.missions)
    events += await publish_threats(kafka_bootstrap(), scenario.threats)
    events += await publish_stocks(kafka_bootstrap(), scenario.stocks)
    events += await publish_weather(kafka_bootstrap(), scenario.cells, scenario.observations)
    return SeedResult(
        targets=len(scenario.targets),
        missions=len(scenario.missions),
        pending=pending,
        events=events,
        threats=len(scenario.threats),
        tracks=len(scenario.tracks),
        active_threats=active,
        weapons=len(scenario.weapons),
        stocks=len(scenario.stocks),
        load_configs=len(scenario.load_configs),
        compatibilities=len(scenario.compatibilities),
        stock_keys=stock_keys,
        cells=len(scenario.cells),
        observations=len(scenario.observations),
    )
