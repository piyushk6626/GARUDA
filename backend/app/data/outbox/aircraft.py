from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...schemas.aircraft import AircraftEvent

STATUS_TOPIC = "garuda.aircraft.status"
EVENTS_TOPIC = "garuda.aircraft.events"
DEAD_LETTER_TOPIC = "garuda.aircraft.dead_letter"


class AircraftOutboxRelay:
    def __init__(self, pool: Any, producer: Any) -> None: self.pool, self.producer = pool, producer

    async def relay_once(self, limit: int = 100) -> int:
        rows = await self.pool.fetch("""SELECT * FROM aircraft.aircraft_outbox WHERE (status_published_at IS NULL OR events_published_at IS NULL) AND next_attempt_at <= clock_timestamp() ORDER BY tail_no,version LIMIT $1""", limit)
        count = 0
        for row in rows:
            event = AircraftEvent.model_validate(row["envelope"])
            try:
                if row["status_published_at"] is None:
                    await self.producer.send_and_wait(STATUS_TOPIC, event.model_dump_json().encode(), key=event.entity_id.encode())
                    await self.pool.execute("UPDATE aircraft.aircraft_outbox SET status_published_at=clock_timestamp() WHERE event_id=$1", event.event_id)
                if row["events_published_at"] is None:
                    await self.producer.send_and_wait(EVENTS_TOPIC, event.model_dump_json().encode(), key=event.entity_id.encode())
                    await self.pool.execute("UPDATE aircraft.aircraft_outbox SET events_published_at=clock_timestamp() WHERE event_id=$1", event.event_id)
                count += 1
            except Exception:
                await self.pool.execute("UPDATE aircraft.aircraft_outbox SET retry_count=retry_count+1,next_attempt_at=clock_timestamp()+interval '5 seconds' WHERE event_id=$1", event.event_id)
                raise
        return count
