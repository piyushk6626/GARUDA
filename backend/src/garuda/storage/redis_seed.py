from redis.asyncio import Redis

from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import WeaponStock


async def seed_redis(
    url: str,
    missions: list[Mission],
    threats: list[Threat],
    stocks: list[WeaponStock],
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
        return len(pending), active, len(stocks)
    finally:
        await client.aclose()


def _stock_key(stock: WeaponStock) -> str:
    return f"stock:{stock.base_id}:{stock.weapon_code}"
