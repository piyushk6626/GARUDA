from __future__ import annotations

import hashlib
import json
from typing import Any

from ...schemas.aircraft import AircraftEvent


class AircraftHistoryWriter:
    def __init__(self, pool: Any) -> None: self.pool = pool
    async def insert(self, event: AircraftEvent) -> bool:
        state, payload_hash = event.state(), hashlib.sha256(event.model_dump_json().encode()).hexdigest()
        async with self.pool.acquire() as connection, connection.transaction():
            identity = await connection.fetchrow("SELECT payload_hash FROM aircraft.history_event_identity WHERE event_id=$1", event.event_id)
            if identity:
                if identity["payload_hash"] != payload_hash: raise ValueError("event identity payload conflict")
                return False
            prior = await connection.fetchrow("SELECT payload_hash FROM aircraft.history_event_identity WHERE run_id=$1 AND tail_no=$2 AND version=$3", state.run_id,state.tail_no,state.version)
            if prior and prior["payload_hash"] != payload_hash: raise ValueError("version payload conflict")
            if prior: return False
            await connection.execute("INSERT INTO aircraft.history_event_identity(event_id,run_id,tail_no,version,payload_hash,logical_time) VALUES($1,$2,$3,$4,$5,$6)",event.event_id,state.run_id,state.tail_no,state.version,payload_hash,event.ts)
            await connection.execute("""INSERT INTO aircraft.aircraft_status_history(time,run_id,tail_no,version,event_id,status,status_reason,base_id,fuel_kg,flight_hours_total,hours_since_inspection,hours_to_next_inspection,maint_return_eta,capabilities,status_since,ready_at,event_kind,metadata)
              VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)""", event.ts,state.run_id,state.tail_no,state.version,event.event_id,state.status.value,state.status_reason,state.base_id,state.fuel_kg,state.flight_hours_total,state.hours_since_inspection,state.hours_to_next_inspection,state.maint_return_eta,list(state.capabilities),state.status_since,state.ready_at,event.payload["kind"],json.dumps(event.payload["metadata"]))
            await connection.execute("UPDATE aircraft.aircraft_outbox SET history_projected_at=clock_timestamp() WHERE event_id=$1", event.event_id)
        return True


class AircraftHistoryConsumer:
    def __init__(self, consumer: Any, writer: AircraftHistoryWriter) -> None: self.consumer,self.writer=consumer,writer
    async def process_one(self) -> bool:
        message = await self.consumer.getone(); event = AircraftEvent.model_validate_json(message.value)
        await self.writer.insert(event); await self.consumer.commit(); return True
