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
    assert result.events == len(scenario.missions)

    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        stored = await connection.fetchval("SELECT count(*) FROM mission")
    finally:
        await connection.close()
    assert stored == len(scenario.missions)

    client = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        pending = await client.zcard("missions:pending")
    finally:
        await client.aclose()
    assert pending == len(scenario.missions)
