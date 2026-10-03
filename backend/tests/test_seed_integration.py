import asyncio
import os
from pathlib import Path

import pytest

from garuda.config import load_config
from garuda.generators.missions import build_scenario

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"

pytestmark = pytest.mark.skipif(
    os.environ.get("GARUDA_INTEGRATION") != "1",
    reason="set GARUDA_INTEGRATION=1 with the Compose stack running",
)


def test_seed_roundtrip() -> None:
    asyncio.run(_seed_roundtrip())


async def _seed_roundtrip() -> None:
    import asyncpg
    from redis.asyncio import Redis

    from garuda.storage.seed import write_all

    scenario = build_scenario(load_config(CONFIG_PATH), 42)
    result = await write_all(scenario)
    assert result.missions == len(scenario.missions)
    assert result.pending == len(scenario.missions)
    assert result.threats == len(scenario.threats)
    assert result.tracks == len(scenario.tracks)
    assert result.active_threats == sum(threat.status == "ACTIVE" for threat in scenario.threats)
    assert result.events == len(scenario.missions) + len(scenario.threats)

    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        stored = await connection.fetchval("SELECT count(*) FROM mission")
        stored_threats = await connection.fetchval("SELECT count(*) FROM threat")
        stored_tracks = await connection.fetchval("SELECT count(*) FROM threat_track")
    finally:
        await connection.close()
    assert stored == len(scenario.missions)
    assert stored_threats == len(scenario.threats)
    assert stored_tracks == len(scenario.tracks)

    client = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        pending = await client.zcard("missions:pending")
        active = await client.zcard("threat:active")
    finally:
        await client.aclose()
    assert pending == len(scenario.missions)
    assert active == result.active_threats
