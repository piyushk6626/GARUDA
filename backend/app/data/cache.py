"""Redis live projection. Version comparison and expiry happen atomically."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..schemas.adsb import PositionObservation

UPDATE_SCRIPT = """
local old = redis.call('HGET', KEYS[3], 'version')
if old and tonumber(old) >= tonumber(ARGV[1]) then return 0 end
if tonumber(ARGV[2]) <= tonumber(ARGV[3]) then return -1 end
redis.call('GEOADD', KEYS[1], ARGV[4], ARGV[5], ARGV[6])
redis.call('HSET', KEYS[3], 'version', ARGV[1], 'deadline', ARGV[2], 'payload', ARGV[7])
redis.call('EXPIREAT', KEYS[3], math.ceil(ARGV[2]))
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[6])
return 1
"""
JANITOR_SCRIPT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, id in ipairs(expired) do
  redis.call('ZREM', KEYS[1], id)
  redis.call('ZREM', KEYS[2], id)
  redis.call('DEL', ARGV[2] .. id)
end
return expired
"""


class RedisLiveCache:
    def __init__(self, client: Any, *, prefix: str = "adsb", freshness_seconds: int = 120) -> None:
        self.client, self.prefix, self.freshness_seconds = client, prefix, freshness_seconds

    @property
    def geo_key(self) -> str: return f"{self.prefix}:live"
    @property
    def expiry_key(self) -> str: return f"{self.prefix}:live:expires"
    def track_key(self, icao24: str) -> str: return f"{self.prefix}:track:{icao24}"

    async def update(self, observation: PositionObservation, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        deadline = observation.time.timestamp() + self.freshness_seconds
        value = await self.client.eval(UPDATE_SCRIPT, 3, self.geo_key, self.expiry_key,
            self.track_key(observation.icao24), observation.version, deadline, now.timestamp(),
            observation.lon, observation.lat, observation.icao24, observation.model_dump_json())
        return value == 1

    async def prune(self, *, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        return await self.client.eval(JANITOR_SCRIPT, 2, self.geo_key, self.expiry_key,
            now.timestamp(), f"{self.prefix}:track:")

    async def get(self, icao24: str, *, now: datetime | None = None) -> PositionObservation | None:
        now = now or datetime.now(timezone.utc)
        data = await self.client.hgetall(self.track_key(icao24))
        if not data or float(data.get("deadline", 0)) <= now.timestamp():
            return None
        return PositionObservation.model_validate_json(data["payload"])

    async def rebuild(self, observations: list[PositionObservation], *, now: datetime | None = None) -> int:
        updated = 0
        for observation in sorted(observations, key=lambda item: (item.icao24, item.version)):
            updated += bool(await self.update(observation, now=now))
        return updated
