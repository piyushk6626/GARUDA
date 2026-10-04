"""Independent live projection consumer."""
from __future__ import annotations

from aiokafka import AIOKafkaConsumer

from ...schemas.adsb import EventEnvelope


class LiveConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, cache) -> None:
        self.consumer, self.cache = consumer, cache
    async def process_one(self) -> bool:
        message = await self.consumer.getone()
        event = EventEnvelope.model_validate_json(message.value)
        await self.cache.update(event.observation())
        await self.consumer.commit()
        return True
