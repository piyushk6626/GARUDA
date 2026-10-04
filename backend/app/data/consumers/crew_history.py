from __future__ import annotations
import hashlib,json
from typing import Any
from ...schemas.crew import CrewEvent
from ..fatigue.crew import fatigue_band
class CrewHistoryWriter:
    def __init__(self,pool:Any)->None:self.pool=pool
    async def insert(self,event:CrewEvent)->bool:
        state=event.state(); digest=hashlib.sha256(json.dumps(event.model_dump(mode="json"),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
        async with self.pool.acquire() as c, c.transaction():
            old=await c.fetchrow("SELECT payload_hash FROM crew.crew_event_identity WHERE event_id=$1",event.event_id)
            if old:
                envelope=await c.fetchval("SELECT envelope FROM crew.crew_event_identity WHERE event_id=$1",event.event_id)
                original=CrewEvent.model_validate_json(envelope) if isinstance(envelope,str) else CrewEvent.model_validate(envelope)
                original_digest=hashlib.sha256(json.dumps(original.model_dump(mode="json"),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
                if original_digest!=digest:raise ValueError("event identity conflict")
            present=await c.fetchval("SELECT 1 FROM crew.crew_duty_log WHERE time=$1 AND run_id=$2 AND crew_id=$3 AND version=$4",event.ts,state.run_id,state.crew_id,state.version)
            if present:return False
            if not old: await c.execute("INSERT INTO crew.crew_event_identity(event_id,run_id,crew_id,version,payload_hash,logical_time,envelope) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)",event.event_id,state.run_id,state.crew_id,state.version,digest,event.ts,event.model_dump_json())
            await c.execute("INSERT INTO crew.crew_duty_log(time,run_id,crew_id,version,event_id,duty_hours_24h,rest_hours_since_last_duty,flight_hours_last_30d,fatigue_score,fatigue_band,state,model_version) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)",event.ts,state.run_id,state.crew_id,state.version,event.event_id,state.duty_hours_24h,state.rest_hours_since_last_duty,state.flight_hours_last_30d,state.fatigue_score,fatigue_band(state.fatigue_score),state.model_dump_json(),event.payload["model_version"])
            await c.execute("UPDATE crew.crew_outbox SET history_projected_at=clock_timestamp() WHERE event_id=$1",event.event_id)
        return True
class CrewHistoryConsumer:
    def __init__(self,consumer:Any,writer:CrewHistoryWriter)->None:self.consumer,self.writer=consumer,writer
    async def process_one(self)->bool:
        message=await self.consumer.getone();await self.writer.insert(CrewEvent.model_validate_json(message.value));await self.consumer.commit();return True
