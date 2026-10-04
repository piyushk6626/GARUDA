"""Kafka event publishing: one envelope, one producer, one table of domains."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer

from garuda.common import as_utc as _as_utc
from garuda.generators.weather import representative_observations
from garuda.schemas.adsb import AdsbTrack
from garuda.schemas.mission import GeneratedScenario
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

# (scenario attribute, topic, event type, id attribute)
ENTITY_TOPICS = (
    ("missions", TOPIC, "mission.requested", "mission_id"),
    ("threats", THREAT_TOPIC, "threat.updated", "threat_id"),
    ("stocks", STOCK_TOPIC, "weapons.stock.updated", "stock_id"),
    ("aircraft", AIRCRAFT_TOPIC, "aircraft.status.changed", "tail_no"),
    ("crew", CREW_TOPIC, "crew.status.changed", "crew_id"),
    ("zones", AIRSPACE_TOPIC, "airspace.activation", "zone_id"),
)


def _stamp(moment: datetime | None = None) -> str:
    return _as_utc(moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(
    topic: str,
    event_type: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    identity: str | None = None,
    version: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid5(NAMESPACE, f"{topic}:{identity or entity_id}")),
        "type": event_type,
        "entity_id": entity_id,
        "ts": _stamp(now),
        "version": version,
        "payload": payload,
    }


def _entity_event(topic: str, event_type: str, entity_id: str, item: Any, now: datetime | None) -> dict:
    return envelope(topic, event_type, entity_id, item.model_dump(mode="json"), now=now)


def mission_event(mission, now: datetime | None = None) -> dict:
    return _entity_event(TOPIC, "mission.requested", mission.mission_id, mission, now)


def threat_event(threat, now: datetime | None = None) -> dict:
    return _entity_event(THREAT_TOPIC, "threat.updated", threat.threat_id, threat, now)


def stock_event(stock, now: datetime | None = None) -> dict:
    return _entity_event(STOCK_TOPIC, "weapons.stock.updated", stock.stock_id, stock, now)


def adsb_event(track: AdsbTrack) -> dict:
    stamp = _stamp(track.time)
    return envelope(
        ADSB_TOPIC,
        "adsb.position.observed",
        track.icao24,
        track.model_dump(mode="json"),
        identity=f"{track.source_id}:{track.icao24}:{stamp}",
        version=int(track.time.timestamp()),
        now=track.time,
    )


def weather_events(
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
    now: datetime | None = None,
) -> list[dict]:
    events: list[dict] = []
    grouped: dict[str, list[WeatherCell]] = {}
    for cell in cells:
        grouped.setdefault(cell.source, []).append(cell)
    for source in sorted(grouped):
        group = grouped[source]
        issued = _stamp(min(cell.issued_at for cell in group))
        valid = [_as_utc(cell.time) for cell in group]
        events.append(
            envelope(
                WEATHER_TOPIC,
                "weather.grid.issued",
                issued,
                {
                    "source": source,
                    "issued_at": issued,
                    "valid_start": _stamp(min(valid)),
                    "valid_end": _stamp(max(valid)),
                    "cell_count": len(group),
                },
                identity=f"grid:{source}:{issued}",
                now=now,
            )
        )
    for obs in representative_observations(observations):
        events.append(
            envelope(
                WEATHER_TOPIC,
                "weather.base.updated",
                obs.icao,
                obs.model_dump(mode="json"),
                identity=f"base:{obs.source}:{obs.icao}",
                now=now,
            )
        )
    return events


def scenario_events(scenario: GeneratedScenario) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for attr, topic, event_type, id_attr in ENTITY_TOPICS:
        events += [
            (topic, _entity_event(topic, event_type, str(getattr(item, id_attr)), item, None))
            for item in getattr(scenario, attr)
        ]
    events += [(WEATHER_TOPIC, e) for e in weather_events(scenario.cells, scenario.observations)]
    events += [(ADSB_TOPIC, adsb_event(track)) for track in scenario.adsb_tracks]
    return events


async def _send(bootstrap: str, events: list[tuple[str, dict]]) -> int:
    if not events:
        return 0
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
    await producer.start()
    try:
        for topic, event in events:
            await producer.send_and_wait(
                topic,
                key=str(event["entity_id"]).encode(),
                value=json.dumps(event).encode(),
            )
        return len(events)
    finally:
        await producer.stop()


async def publish_scenario(bootstrap: str, scenario: GeneratedScenario) -> int:
    return await _send(bootstrap, scenario_events(scenario))


async def publish_weather(
    bootstrap: str,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> int:
    return await _send(bootstrap, [(WEATHER_TOPIC, e) for e in weather_events(cells, observations)])
