import json
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from garuda.generators.weather import representative_observations
from garuda.schemas.adsb import AdsbTrack
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.crew import Crew
from garuda.schemas.mission import Mission
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import WeaponStock
from garuda.schemas.weather import WeatherCell, WeatherStationObs

TOPIC = "garuda.mission.request"
THREAT_TOPIC = "garuda.threat.update"
STOCK_TOPIC = "garuda.weapons.stock"
WEATHER_TOPIC = "garuda.weather.update"
AIRCRAFT_TOPIC = "garuda.aircraft.status"
CREW_TOPIC = "garuda.crew.status"
AIRSPACE_TOPIC = "garuda.airspace.activation"
ADSB_TOPIC = "garuda.adsb.positions"
NAMESPACE = uuid.UUID("6f0b1c3a-7e2d-4a1f-9c55-0b8e4d2a11a0")
THREAT_NAMESPACE = uuid.UUID("c4e1a8b2-6d3f-4a90-8c17-5b0e2f9a6d34")
STOCK_NAMESPACE = uuid.UUID("9a2e6c41-1b70-4d88-a5f3-7c14e0b28d56")
WEATHER_NAMESPACE = uuid.UUID("d17a4e90-5c2b-4f68-9a31-0e6b8c4d2f15")
AIRCRAFT_NAMESPACE = uuid.UUID("a1c4e8b0-2d55-4f19-8a77-6c30b9e14d28")
CREW_NAMESPACE = uuid.UUID("b7e2d091-4c6a-4e83-9f15-2a8d6c0b47e1")
AIRSPACE_NAMESPACE = uuid.UUID("c90f6a33-8b14-4d27-a1e6-5f2c8b7d04aa")
ADSB_NAMESPACE = uuid.UUID("d4a81e57-0f92-4c6b-b338-7e15a9c26d40")


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


def weather_events(
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
    now: datetime | None = None,
) -> list[dict]:
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    events: list[dict] = []
    grouped: dict[str, list[WeatherCell]] = {}
    for cell in cells:
        grouped.setdefault(cell.source, []).append(cell)
    for source in sorted(grouped):
        group = grouped[source]
        issued = min(_as_utc(cell.issued_at) for cell in group)
        valid = [_as_utc(cell.time) for cell in group]
        entity_id = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
        events.append(
            {
                "event_id": str(uuid.uuid5(WEATHER_NAMESPACE, f"{WEATHER_TOPIC}:grid:{source}:{entity_id}")),
                "type": "weather.grid.issued",
                "entity_id": entity_id,
                "ts": stamp,
                "version": 1,
                "payload": {
                    "source": source,
                    "issued_at": entity_id,
                    "valid_start": min(valid).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_end": max(valid).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cell_count": len(group),
                },
            }
        )
    for obs in representative_observations(observations):
        events.append(
            {
                "event_id": str(
                    uuid.uuid5(WEATHER_NAMESPACE, f"{WEATHER_TOPIC}:base:{obs.source}:{obs.icao}")
                ),
                "type": "weather.base.updated",
                "entity_id": obs.icao,
                "ts": stamp,
                "version": 1,
                "payload": obs.model_dump(mode="json"),
            }
        )
    return events


async def publish_weather(
    bootstrap: str,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> int:
    events = weather_events(cells, observations)
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        for event in events:
            await producer.send_and_wait(
                WEATHER_TOPIC,
                key=str(event["entity_id"]).encode(),
                value=json.dumps(event).encode(),
            )
        return len(events)
    finally:
        await producer.stop()


async def publish_aircraft(bootstrap: str, aircraft: list[Aircraft]) -> int:
    return await _publish(bootstrap, AIRCRAFT_TOPIC, [aircraft_event(airframe) for airframe in aircraft])


def aircraft_event(airframe: Aircraft, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(AIRCRAFT_NAMESPACE, f"{AIRCRAFT_TOPIC}:{airframe.tail_no}")),
        "type": "aircraft.status.changed",
        "entity_id": airframe.tail_no,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": airframe.model_dump(mode="json"),
    }


async def publish_crew(bootstrap: str, crew: list[Crew]) -> int:
    return await _publish(bootstrap, CREW_TOPIC, [crew_event(member) for member in crew])


def crew_event(member: Crew, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(CREW_NAMESPACE, f"{CREW_TOPIC}:{member.crew_id}")),
        "type": "crew.status.changed",
        "entity_id": member.crew_id,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": member.model_dump(mode="json"),
    }


async def publish_airspace(bootstrap: str, zones: list[AirspaceZone]) -> int:
    return await _publish(bootstrap, AIRSPACE_TOPIC, [airspace_event(zone) for zone in zones])


def airspace_event(zone: AirspaceZone, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    return {
        "event_id": str(uuid.uuid5(AIRSPACE_NAMESPACE, f"{AIRSPACE_TOPIC}:{zone.zone_id}")),
        "type": "airspace.activation",
        "entity_id": zone.zone_id,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "payload": zone.model_dump(mode="json"),
    }


async def publish_adsb(bootstrap: str, tracks: list[AdsbTrack]) -> int:
    return await _publish(bootstrap, ADSB_TOPIC, [adsb_event(track) for track in tracks])


def adsb_event(track: AdsbTrack) -> dict:
    stamp = track.time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    identity = f"{track.source_id}:{track.icao24}:{stamp}"
    return {
        "event_id": str(uuid.uuid5(ADSB_NAMESPACE, f"{ADSB_TOPIC}:{identity}")),
        "type": "adsb.position.observed",
        "entity_id": track.icao24,
        "ts": stamp,
        "version": int(track.time.timestamp()),
        "payload": track.model_dump(mode="json"),
    }


async def _publish(bootstrap: str, topic: str, events: list[dict]) -> int:
    if not events:
        return 0
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        for event in events:
            await producer.send_and_wait(
                topic,
                key=str(event["entity_id"]).encode(),
                value=json.dumps(event).encode(),
            )
        return len(events)
    finally:
        await producer.stop()


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
