"""Kafka publisher with explicit acknowledgement and bounded retries."""
from __future__ import annotations

import asyncio
import json
from typing import Iterable

from aiokafka import AIOKafkaProducer

from ..schemas.adsb import EventEnvelope


class KafkaPositionPublisher:
    def __init__(self, brokers: str, topic: str = "garuda.adsb.positions") -> None:
        self.brokers, self.topic = brokers, topic
        self.producer = AIOKafkaProducer(bootstrap_servers=brokers, enable_idempotence=True,
            acks="all", value_serializer=lambda item: json.dumps(item, sort_keys=True).encode())

    async def start(self) -> None: await self.producer.start()
    async def stop(self) -> None: await self.producer.stop()

    async def publish(self, event: EventEnvelope) -> None:
        await self.producer.send_and_wait(self.topic, key=event.entity_id.encode(),
                                         value=event.model_dump(mode="json"))

    async def publish_many(self, events: Iterable[EventEnvelope]) -> int:
        count = 0
        for event in events:
            await self.publish(event)
            count += 1
        return count
