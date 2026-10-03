from redis.asyncio import Redis

from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat


async def seed_redis(url: str, missions: list[Mission], threats: list[Threat]) -> tuple[int, int]:
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
        pipe = client.pipeline()
        pipe.delete("missions:pending")
        pipe.delete("threat:active")
        if pending:
            pipe.zadd("missions:pending", pending)
        if places:
            pipe.geoadd("threat:active", places)
        await pipe.execute()
        return len(pending), active
    finally:
        await client.aclose()
