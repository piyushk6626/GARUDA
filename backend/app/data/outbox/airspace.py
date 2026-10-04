from __future__ import annotations
from typing import Any
from ...schemas.airspace import AirspaceEvent
ACTIVATION_TOPIC="garuda.airspace.activation"; DEAD_LETTER_TOPIC="garuda.airspace.dead_letter"
class AirspaceOutboxRelay:
    def __init__(self,pool:Any,producer:Any)->None:self.pool,self.producer=pool,producer
    async def relay_once(self,limit:int=100)->int:
        rows=await self.pool.fetch("SELECT * FROM airspace.airspace_outbox WHERE published_at IS NULL AND next_attempt_at<=clock_timestamp() ORDER BY zone_id,version LIMIT $1",limit); count=0
        for row in rows:
            event=AirspaceEvent.model_validate_json(row["envelope"]) if isinstance(row["envelope"],str) else AirspaceEvent.model_validate(row["envelope"])
            try:
                await self.producer.send_and_wait(ACTIVATION_TOPIC,event.model_dump_json().encode(),key=event.entity_id.encode())
                await self.pool.execute("UPDATE airspace.airspace_outbox SET published_at=clock_timestamp() WHERE event_id=$1",event.event_id); count+=1
            except Exception:
                await self.pool.execute("UPDATE airspace.airspace_outbox SET retry_count=retry_count+1,next_attempt_at=clock_timestamp()+interval '5 seconds' WHERE event_id=$1",event.event_id); raise
        return count
