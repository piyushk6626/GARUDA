from __future__ import annotations

from typing import Any
from ...schemas.aircraft import AircraftEvent, AircraftStatus

LUA = """
local old = redis.call('HGET', KEYS[1], 'version')
if old and tonumber(old) >= tonumber(ARGV[1]) then return 0 end
local oldbase = redis.call('HGET', KEYS[1], 'base_id')
local oldstatus = redis.call('HGET', KEYS[1], 'status')
if oldbase and oldstatus == 'FMC' then redis.call('SREM', 'base:' .. oldbase .. ':fmc', ARGV[2]) end
redis.call('HSET', KEYS[1], 'version',ARGV[1],'tail_no',ARGV[2],'run_id',ARGV[3],'status',ARGV[4],'reason',ARGV[5],'fuel_kg',ARGV[6],'base_id',ARGV[7],'maint_return_eta',ARGV[8],'ready_at',ARGV[9],'updated_at',ARGV[10],'last_event_id',ARGV[11])
if ARGV[4] == 'FMC' then redis.call('SADD', 'base:' .. ARGV[7] .. ':fmc', ARGV[2]) end
return 1
"""

class AircraftLiveProjection:
    def __init__(self, client: Any) -> None: self.client=client
    async def apply(self,event: AircraftEvent) -> bool:
        state=event.state(); result=await self.client.eval(LUA,1,f"ac:{state.tail_no}",state.version,state.tail_no,state.run_id,state.status.value,state.status_reason or "",state.fuel_kg,state.base_id,state.maint_return_eta.isoformat() if state.maint_return_eta else "",state.ready_at.isoformat(),state.updated_at.isoformat(),str(event.event_id)); return bool(result)

class AircraftLiveConsumer:
    def __init__(self,consumer: Any,projection: AircraftLiveProjection)->None:self.consumer,self.projection=consumer,projection
    async def process_one(self)->bool:
        message=await self.consumer.getone(); await self.projection.apply(AircraftEvent.model_validate_json(message.value)); await self.consumer.commit(); return True
