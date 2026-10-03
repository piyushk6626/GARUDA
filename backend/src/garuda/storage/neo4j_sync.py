from neo4j import AsyncGraphDatabase

from garuda.schemas.mission import Mission, Target
from garuda.schemas.threat import Threat

_CLEAR = """
MATCH (n)
WHERE n:Mission OR n:Target OR n:Threat
DETACH DELETE n
"""

_TARGETS = """
UNWIND $rows AS row
MERGE (t:Target {target_id: row.target_id})
SET t.class = row.class,
    t.lat = row.lat,
    t.lon = row.lon,
    t.value = row.value,
    t.hardness = row.hardness
"""

_MISSIONS = """
UNWIND $rows AS row
MERGE (m:Mission {mission_id: row.mission_id})
SET m.type = row.type,
    m.priority = row.priority,
    m.value = row.value,
    m.status = row.status,
    m.window_start = row.window_start,
    m.window_end = row.window_end
"""

_LINKS = """
UNWIND $rows AS row
MATCH (m:Mission {mission_id: row.mission_id})
MATCH (t:Target {target_id: row.target_id})
MERGE (m)-[:TARGETS]->(t)
"""

_THREATS = """
UNWIND $rows AS row
MERGE (th:Threat {threat_id: row.threat_id})
SET th.type = row.type,
    th.lat = row.lat,
    th.lon = row.lon,
    th.range_km = row.range_km,
    th.confidence = row.confidence,
    th.last_seen = row.last_seen,
    th.status = row.status,
    th.mobile = row.mobile
"""

_COVERS = """
UNWIND $rows AS row
MATCH (th:Threat {threat_id: row.threat_id})
MATCH (t:Target {target_id: row.target_id})
MERGE (th)-[:COVERS]->(t)
"""


async def sync_neo4j(
    uri: str,
    user: str,
    password: str,
    targets: list[Target],
    missions: list[Mission],
    threats: list[Threat],
) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with driver.session() as session:
            await session.execute_write(
                _write_graph,
                _target_rows(targets),
                _mission_rows(missions),
                _threat_rows(threats),
                _cover_rows(targets),
            )
    finally:
        await driver.close()


async def _write_graph(
    tx,
    target_rows: list[dict],
    mission_rows: list[dict],
    threat_rows: list[dict],
    cover_rows: list[dict],
) -> None:
    await _consume(tx, _CLEAR)
    await _consume(tx, _TARGETS, rows=target_rows)
    await _consume(tx, _MISSIONS, rows=mission_rows)
    await _consume(tx, _THREATS, rows=threat_rows)
    linked = [row for row in mission_rows if row["target_id"] is not None]
    if linked:
        await _consume(tx, _LINKS, rows=linked)
    if cover_rows:
        await _consume(tx, _COVERS, rows=cover_rows)


async def _consume(tx, query: str, **kwargs: object) -> None:
    result = await tx.run(query, **kwargs)
    await result.consume()


def _target_rows(targets: list[Target]) -> list[dict]:
    return [
        {
            "target_id": target.target_id,
            "class": target.target_class,
            "lat": target.lat,
            "lon": target.lon,
            "value": target.value,
            "hardness": target.hardness,
        }
        for target in targets
    ]


def _threat_rows(threats: list[Threat]) -> list[dict]:
    return [
        {
            "threat_id": threat.threat_id,
            "type": threat.type,
            "lat": threat.lat,
            "lon": threat.lon,
            "range_km": threat.engagement_range_km,
            "confidence": threat.confidence,
            "last_seen": threat.last_seen.isoformat(),
            "status": threat.status,
            "mobile": threat.mobile,
        }
        for threat in threats
    ]


def _cover_rows(targets: list[Target]) -> list[dict]:
    return [
        {"threat_id": threat_id, "target_id": target.target_id}
        for target in targets
        for threat_id in target.defended_by
    ]


def _mission_rows(missions: list[Mission]) -> list[dict]:
    return [
        {
            "mission_id": mission.mission_id,
            "type": mission.type,
            "priority": mission.priority,
            "value": mission.value,
            "status": mission.status,
            "window_start": mission.window_start.isoformat(),
            "window_end": mission.window_end.isoformat(),
            "target_id": mission.target_id,
        }
        for mission in missions
    ]
