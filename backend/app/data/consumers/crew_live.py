from __future__ import annotations
import json
from typing import Any
from ...schemas.crew import CrewEvent
LUA="""local old=redis.call('HGET',KEYS[1],'version'); if old and tonumber(old)>=tonumber(ARGV[1]) then return 0 end; redis.call('HSET',KEYS[1],'version',ARGV[1],'event_id',ARGV[2],'run_id',ARGV[3],'state',ARGV[4]); return 1"""
class CrewLiveProjection:
    def __init__(self,client:Any)->None:self.client=client
    async def apply(self,event:CrewEvent)->bool:
        state=event.state();value=json.dumps(state.model_dump(mode="json"),sort_keys=True,separators=(",",":"));return bool(await self.client.eval(LUA,1,f"crew:{state.crew_id}",state.version,str(event.event_id),state.run_id,value))
class CrewLiveConsumer:
    def __init__(self,consumer:Any,projection:CrewLiveProjection)->None:self.consumer,self.projection=consumer,projection
    async def process_one(self)->bool:
        message=await self.consumer.getone();await self.projection.apply(CrewEvent.model_validate_json(message.value));await self.consumer.commit();return True
