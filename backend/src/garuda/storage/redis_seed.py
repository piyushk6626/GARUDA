from redis.asyncio import Redis

from garuda.schemas.mission import Mission


async def seed_redis(url: str, missions: list[Mission]) -> int:
    client = Redis.from_url(url)
    try:
        pending = {
            mission.mission_id: mission.priority
            for mission in missions
            if mission.status == "REQUESTED"
        }
        pipe = client.pipeline()
        pipe.delete("missions:pending")
        if pending:
            pipe.zadd("missions:pending", pending)
        await pipe.execute()
        return len(pending)
    finally:
        await client.aclose()
