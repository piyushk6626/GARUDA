import json
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import WeaponStock

TOPIC = "garuda.mission.request"
THREAT_TOPIC = "garuda.threat.update"
STOCK_TOPIC = "garuda.weapons.stock"
NAMESPACE = uuid.UUID("6f0b1c3a-7e2d-4a1f-9c55-0b8e4d2a11a0")
THREAT_NAMESPACE = uuid.UUID("c4e1a8b2-6d3f-4a90-8c17-5b0e2f9a6d34")
STOCK_NAMESPACE = uuid.UUID("9a2e6c41-1b70-4d88-a5f3-7c14e0b28d56")


async def publish_missions(bootstrap: str, missions: list[Mission]) -> int:
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        published = 0
        for mission in missions:
            event = mission_event(mission)
            await producer.send_and_wait(
                TOPIC,
                key=mission.mission_id.encode(),
                value=json.dumps(event).encode(),
            )
            published += 1
        return published
    finally:
        await producer.stop()


def mission_event(mission: Mission, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(NAMESPACE, f"{TOPIC}:{mission.mission_id}")),
        "type": "mission.requested",
        "entity_id": mission.mission_id,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": mission.model_dump(mode="json"),
    }


async def publish_threats(bootstrap: str, threats: list[Threat]) -> int:
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        published = 0
        for threat in threats:
            event = threat_event(threat)
            await producer.send_and_wait(
                THREAT_TOPIC,
                key=threat.threat_id.encode(),
                value=json.dumps(event).encode(),
            )
            published += 1
        return published
    finally:
        await producer.stop()


def threat_event(threat: Threat, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(THREAT_NAMESPACE, f"{THREAT_TOPIC}:{threat.threat_id}")),
        "type": "threat.updated",
        "entity_id": threat.threat_id,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": threat.model_dump(mode="json"),
    }


async def publish_stocks(bootstrap: str, stocks: list[WeaponStock]) -> int:
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        published = 0
        for stock in stocks:
            event = stock_event(stock)
            await producer.send_and_wait(
                STOCK_TOPIC,
                key=stock.stock_id.encode(),
                value=json.dumps(event).encode(),
            )
            published += 1
        return published
    finally:
        await producer.stop()


def stock_event(stock: WeaponStock, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(STOCK_NAMESPACE, f"{STOCK_TOPIC}:{stock.stock_id}")),
        "type": "weapons.stock.updated",
        "entity_id": stock.stock_id,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": stock.model_dump(mode="json"),
    }
