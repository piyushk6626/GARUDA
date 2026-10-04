import json
from datetime import datetime, timezone

from redis.asyncio import Redis

from garuda.generators.weather import representative_observations
from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import WeaponStock
from garuda.schemas.weather import WeatherCell, WeatherStationObs


async def seed_redis(
    url: str,
    missions: list[Mission],
    threats: list[Threat],
    stocks: list[WeaponStock],
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> tuple[int, int, int]:
    client = Redis.from_url(url)
    try:
        pending = {
            mission.mission_id: mission.priority
            for mission in missions
            if mission.status == "REQUESTED"
        }
        places: list[float | str] = []
        active = 0
        for threat in threats:
            if threat.status != "ACTIVE":
                continue
            places.extend((threat.lon, threat.lat, threat.threat_id))
            active += 1
        stale = [key async for key in client.scan_iter(match="stock:*", count=200)]
        pipe = client.pipeline()
        pipe.delete("missions:pending")
        pipe.delete("threat:active")
        if stale:
            pipe.delete(*stale)
        if pending:
            pipe.zadd("missions:pending", pending)
        if places:
            pipe.geoadd("threat:active", places)
        for stock in stocks:
            pipe.set(_stock_key(stock), stock.quantity)
        await pipe.execute()
        await write_weather_cache(client, cells, observations)
        return len(pending), active, len(stocks)
    finally:
        await client.aclose()


def _stock_key(stock: WeaponStock) -> str:
    return f"stock:{stock.base_id}:{stock.weapon_code}"


async def write_weather_cache(
    client: Redis,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> None:
    stale_grid = [key async for key in client.scan_iter(match="wx:grid:*", count=500)]
    stale_base = [key async for key in client.scan_iter(match="wx:base:*", count=50)]
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for cell in cells:
        hour = _hour_stamp(cell.time)
        grouped.setdefault(hour, {})[f"{cell.lat:.1f},{cell.lon:.1f}"] = {
            "flyability": cell.flyability,
            "ceiling_ft": cell.ceiling_ft,
            "vis_km": cell.vis_km,
            "wind_kts": cell.wind_kts,
            "cb_flag": cell.cb_flag,
        }
    pipe = client.pipeline()
    if stale_grid:
        pipe.delete(*stale_grid)
    if stale_base:
        pipe.delete(*stale_base)
    for hour, payload in grouped.items():
        pipe.set(f"wx:grid:{hour}", json.dumps(payload))
    for obs in representative_observations(observations):
        pipe.set(f"wx:base:{obs.icao}", json.dumps(obs.model_dump(mode="json")))
    await pipe.execute()


def _hour_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
