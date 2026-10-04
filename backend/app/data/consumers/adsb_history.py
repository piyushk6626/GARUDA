"""At-least-once history worker: write first, commit only after a successful write."""
from __future__ import annotations

from typing import Protocol

from aiokafka import AIOKafkaConsumer

from ...schemas.adsb import EventEnvelope, PositionObservation


class HistoryWriter(Protocol):
    async def insert(self, observation: PositionObservation) -> bool: ...


class HistoryConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, writer: HistoryWriter) -> None:
        self.consumer, self.writer = consumer, writer

    async def process_one(self) -> bool:
        message = await self.consumer.getone()
        event = EventEnvelope.model_validate_json(message.value)
        await self.writer.insert(event.observation())  # duplicate-safe database insert
        await self.consumer.commit()  # a crash before this point is intentionally replayed
        return True


class AsyncpgHistoryWriter:
    def __init__(self, pool) -> None: self.pool = pool
    async def insert(self, item: PositionObservation) -> bool:
        query = """INSERT INTO adsb.adsb_track
        (time,source_id,icao24,callsign,origin_country,last_contact,lat,lon,alt_m,geo_alt_m,
         speed_ms,heading,vrate,on_ground,position_source,ingested_at,snapshot_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT DO NOTHING"""
        async with self.pool.acquire() as connection:
            result = await connection.execute(query, item.time, item.source_id, item.icao24, item.callsign,
                item.origin_country, item.last_contact, item.lat, item.lon, item.alt_m, item.geo_alt_m,
                item.speed_ms, item.heading, item.vrate, item.on_ground, item.position_source,
                item.ingested_at, item.snapshot_key)
        return result.endswith("1")
