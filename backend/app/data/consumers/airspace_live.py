"""Run-isolated, version guarded active-set Redis projection."""
from __future__ import annotations
from typing import Any
from ...schemas.airspace import AirspaceEvent
LUA="""
local old=redis.call('HGET',KEYS[1],'version')
if old and tonumber(old)>=tonumber(ARGV[1]) then return 0 end
if redis.call('SISMEMBER',KEYS[2],ARGV[2])==1 then redis.call('SREM',KEYS[2],ARGV[2]) end
if ARGV[3]=='1' then redis.call('SADD',KEYS[2],ARGV[2]) end
redis.call('HSET',KEYS[1],'version',ARGV[1],'run_id',ARGV[4],'active',ARGV[3],'evaluated_at',ARGV[5])
return 1
"""
class AirspaceLiveProjection:
    def __init__(self,client:Any)->None:self.client=client
    async def apply(self,event:AirspaceEvent)->bool:
        z=event.zone(); active="1" if event.payload["active"] else "0"; key=f"airspace:{z.run_id}:zone:{z.zone_id}"
        changed=await self.client.eval(LUA,2,key,f"airspace:{z.run_id}:active",z.version,z.zone_id,active,z.run_id,event.payload["effective_clock"])
        return bool(changed)
    async def refresh(self,run_id:str,events:list[AirspaceEvent],*,clock:str)->None:
        """A staging swap makes an empty set and run changes explicit and atomic."""
        stage=f"airspace:{run_id}:active:staging"; pipe=self.client.pipeline(transaction=True); pipe.delete(stage)
        for event in events:
            if event.payload["active"]: pipe.sadd(stage,event.entity_id)
        pipe.rename(stage,f"airspace:{run_id}:active"); pipe.hset(f"airspace:{run_id}:meta",mapping={"evaluated_at":clock,"run_id":run_id}); pipe.set("airspace:active_run",run_id); await pipe.execute()
class AirspaceLiveConsumer:
    def __init__(self,consumer:Any,projection:AirspaceLiveProjection,dead_producer:Any|None=None)->None:self.consumer,self.projection,self.dead_producer=consumer,projection,dead_producer
    async def process_one(self)->bool:
        message=await self.consumer.getone()
        try: await self.projection.apply(AirspaceEvent.model_validate_json(message.value))
        except Exception:
            if self.dead_producer: await self.dead_producer.send_and_wait("garuda.airspace.dead_letter",message.value,key=message.key)
            await self.consumer.commit(); return False
        await self.consumer.commit(); return True
