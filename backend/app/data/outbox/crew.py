"""Serialized at-least-once relay for crew status and event streams."""
from __future__ import annotations
from typing import Any
from ...schemas.crew import CrewEvent
STATUS_TOPIC="garuda.crew.status"; EVENTS_TOPIC="garuda.crew.events"; DEAD_LETTER_TOPIC="garuda.crew.dead_letter"
class CrewOutboxRelay:
    def __init__(self,pool:Any,producer:Any)->None:self.pool,self.producer=pool,producer
    async def relay_once(self,limit:int=100)->int:
        count=0
        async with self.pool.acquire() as c:
            await c.execute("SELECT pg_advisory_lock(hashtext('crew-outbox-relay'))")
            try:
                rows=await c.fetch("SELECT * FROM crew.crew_outbox WHERE (status_published_at IS NULL OR events_published_at IS NULL) AND next_attempt_at<=clock_timestamp() ORDER BY crew_id,version LIMIT $1",limit)
                for row in rows:
                    event=CrewEvent.model_validate_json(row["envelope"]) if isinstance(row["envelope"],str) else CrewEvent.model_validate(row["envelope"])
                    try:
                        if row["status_published_at"] is None:
                            await self.producer.send_and_wait(STATUS_TOPIC,event.model_dump_json().encode(),key=event.entity_id.encode());await c.execute("UPDATE crew.crew_outbox SET status_published_at=clock_timestamp() WHERE event_id=$1",event.event_id)
                        if row["events_published_at"] is None:
                            await self.producer.send_and_wait(EVENTS_TOPIC,event.model_dump_json().encode(),key=event.entity_id.encode());await c.execute("UPDATE crew.crew_outbox SET events_published_at=clock_timestamp() WHERE event_id=$1",event.event_id)
                        count+=1
                    except Exception:
                        await c.execute("UPDATE crew.crew_outbox SET attempts=attempts+1,next_attempt_at=clock_timestamp()+interval '5 seconds' WHERE event_id=$1",event.event_id);raise
            finally: await c.execute("SELECT pg_advisory_unlock(hashtext('crew-outbox-relay'))")
        return count
