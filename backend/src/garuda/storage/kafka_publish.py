import json
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from garuda.schemas.mission import Mission

TOPIC = "garuda.mission.request"
NAMESPACE = uuid.UUID("6f0b1c3a-7e2d-4a1f-9c55-0b8e4d2a11a0")


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
