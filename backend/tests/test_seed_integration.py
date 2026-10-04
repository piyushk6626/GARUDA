import asyncio
import json
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
    assert result.weapons == len(scenario.weapons)
    assert result.stocks == len(scenario.stocks)
    assert result.load_configs == len(scenario.load_configs)
    assert result.compatibilities == len(scenario.compatibilities)
    assert result.stock_keys == len(scenario.stocks)
    assert result.cells == len(scenario.cells)
    assert result.observations == len(scenario.observations)
    assert result.bases == len(scenario.bases) == 7
    assert result.features == len(scenario.features)
    assert result.borders == len(scenario.borders)
    assert result.distances == len(scenario.bases) * len(scenario.targets)
    assert result.aircraft == len(scenario.aircraft) == 150
    assert result.crew == len(scenario.crew) == 300
    assert result.zones == len(scenario.zones) == 20
    assert result.adsb_tracks == len(scenario.adsb_tracks)
    assert result.events == (
        len(scenario.missions)
        + len(scenario.threats)
        + len(scenario.stocks)
        + 8
        + len(scenario.aircraft)
        + len(scenario.crew)
        + len(scenario.zones)
        + len(scenario.adsb_tracks)
    )

    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        stored = await connection.fetchval("SELECT count(*) FROM mission")
        stored_threats = await connection.fetchval("SELECT count(*) FROM threat")
        stored_tracks = await connection.fetchval("SELECT count(*) FROM threat_track")
        stored_weapons = await connection.fetchval("SELECT count(*) FROM weapon_type")
        stored_stocks = await connection.fetchval("SELECT count(*) FROM weapon_stock")
        stored_loads = await connection.fetchval("SELECT count(*) FROM load_config")
        stored_compat = await connection.fetchval("SELECT count(*) FROM weapon_compatibility")
        stored_cells = await connection.fetchval("SELECT count(*) FROM weather_grid")
        stored_obs = await connection.fetchval("SELECT count(*) FROM weather_station_obs")
        stored_bases = await connection.fetchval("SELECT count(*) FROM base")
        stored_features = await connection.fetchval("SELECT count(*) FROM geo_feature")
        stored_borders = await connection.fetchval("SELECT count(*) FROM border")
        stored_distances = await connection.fetchval("SELECT count(*) FROM base_target_distance")
        stored_aircraft = await connection.fetchval("SELECT count(*) FROM aircraft")
        stored_crew = await connection.fetchval("SELECT count(*) FROM crew")
        stored_zones = await connection.fetchval("SELECT count(*) FROM airspace_zone")
        stored_adsb = await connection.fetchval("SELECT count(*) FROM adsb_track")
    finally:
        await connection.close()
    assert stored == len(scenario.missions)
    assert stored_threats == len(scenario.threats)
    assert stored_tracks == len(scenario.tracks)
    assert stored_weapons == len(scenario.weapons)
    assert stored_stocks == len(scenario.stocks)
    assert stored_loads == len(scenario.load_configs)
    assert stored_compat == len(scenario.compatibilities)
    assert stored_cells == len(scenario.cells)
    assert stored_obs == len(scenario.observations)
    assert stored_bases == len(scenario.bases)
    assert stored_features == len(scenario.features)
    assert stored_borders == len(scenario.borders)
    assert stored_distances == len(scenario.distances)
    assert stored_aircraft == len(scenario.aircraft)
    assert stored_crew == len(scenario.crew)
    assert stored_zones == len(scenario.zones)
    assert stored_adsb == len(scenario.adsb_tracks)

    client = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        pending = await client.zcard("missions:pending")
        active = await client.zcard("threat:active")
        sample = scenario.stocks[0]
        stored_qty = await client.get(f"stock:{sample.base_id}:{sample.weapon_code}")
        viax = await client.get("wx:base:VIAX")
        grid_keys = [key async for key in client.scan_iter(match="wx:grid:*", count=200)]
        base_hash = await client.hgetall("base:B1")
    finally:
        await client.aclose()
    assert pending == len(scenario.missions)
    assert active == result.active_threats
    assert stored_qty is not None
    assert int(stored_qty) == sample.quantity
    assert viax is not None
    assert json.loads(viax)["icao"] == "VIAX"
    assert grid_keys
    fields = {
        (key.decode() if isinstance(key, bytes) else key): (
            value.decode() if isinstance(value, bytes) else value
        )
        for key, value in base_hash.items()
    }
    jodhpur = next(base for base in scenario.bases if base.base_id == "B1")
    assert fields["icao"] == "VIJO"
    assert fields["base_id"] == "B1"
    assert fields["runway_length_m"] == str(jodhpur.runway_length_m)
