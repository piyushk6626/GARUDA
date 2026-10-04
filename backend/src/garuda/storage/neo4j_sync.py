from neo4j import AsyncGraphDatabase

from garuda.bases import BASES
from garuda.generators.weather import cells_for_bases
from garuda.schemas.aircraft import Aircraft, Squadron
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.crew import Crew
from garuda.schemas.gis import BaseRecord
from garuda.schemas.mission import Mission, Target
from garuda.schemas.threat import Threat
from garuda.schemas.weapon import WeaponStock, WeaponType
from garuda.schemas.weather import WeatherCell

_CLEAR = """
MATCH (n)
WHERE n:Mission OR n:Target OR n:Threat OR n:WeaponStock OR n:WeaponType OR n:Base
   OR n:Aircraft OR n:Squadron OR n:Crew OR n:AircraftType OR n:AirspaceZone
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

_WEAPON_TYPES = """
UNWIND $rows AS row
MERGE (w:WeaponType {weapon_code: row.weapon_code})
SET w.category = row.category,
    w.range_km = row.range_km,
    w.weight_kg = row.weight_kg
"""

_STOCKS = """
UNWIND $rows AS row
MERGE (b:Base {base_id: row.base_id})
MERGE (s:WeaponStock {stock_id: row.stock_id})
SET s.base_id = row.base_id,
    s.weapon_code = row.weapon_code,
    s.quantity = row.quantity
WITH s, b, row
MATCH (w:WeaponType {weapon_code: row.weapon_code})
MERGE (s)-[:STORED_AT]->(b)
MERGE (s)-[:OF_TYPE]->(w)
"""

_CLEAR_WEATHER = "MATCH (w:WeatherCell) DETACH DELETE w"

_BASE_IDENTITY = """
UNWIND $rows AS row
MERGE (b:Base {base_id: row.base_id})
SET b.name = row.name,
    b.icao = row.icao,
    b.lat = row.lat,
    b.lon = row.lon,
    b.elevation_ft = row.elevation_ft
"""

_BASES = """
UNWIND $rows AS row
MERGE (b:Base {base_id: row.base_id})
SET b.name = row.name,
    b.icao = row.icao,
    b.lat = row.lat,
    b.lon = row.lon,
    b.elevation_ft = row.elevation_ft,
    b.runway_count = row.runway_count,
    b.runway_length_m = row.runway_length_m,
    b.shelter_count = row.shelter_count,
    b.fuel_capacity_l = row.fuel_capacity_l,
    b.ground_crew_teams = row.ground_crew_teams,
    b.max_sorties_per_hour = row.max_sorties_per_hour
"""

_WEATHER = """
UNWIND $rows AS row
MERGE (w:WeatherCell {cell_id: row.cell_id})
SET w.lat = row.lat,
    w.lon = row.lon,
    w.valid_time = row.valid_time,
    w.ceiling_ft = row.ceiling_ft,
    w.vis_km = row.vis_km,
    w.wind_kts = row.wind_kts,
    w.cb_flag = row.cb_flag,
    w.flyability = row.flyability,
    w.source = row.source
WITH w, row
MATCH (b:Base {base_id: row.base_id})
MERGE (w)-[:AFFECTS]->(b)
"""

_SQUADRONS = """
UNWIND $rows AS row
MERGE (s:Squadron {sqn_id: row.sqn_id})
SET s.type = row.type,
    s.name = row.name,
    s.home_base_id = row.home_base_id
WITH s, row
MATCH (b:Base {base_id: row.home_base_id})
MERGE (s)-[:BASED_AT]->(b)
"""

_AIRCRAFT = """
UNWIND $rows AS row
MERGE (a:Aircraft {tail_no: row.tail_no})
SET a.type = row.type,
    a.status = row.status,
    a.base_id = row.base_id,
    a.fuel_kg = row.fuel_kg
WITH a, row
MATCH (s:Squadron {sqn_id: row.sqn_id})
MATCH (b:Base {base_id: row.base_id})
MERGE (a)-[:ASSIGNED_TO]->(s)
MERGE (a)-[:LOCATED_AT]->(b)
"""

_CREW = """
UNWIND $rows AS row
MERGE (c:Crew {crew_id: row.crew_id})
SET c.role = row.role,
    c.callsign = row.callsign,
    c.base_id = row.base_id,
    c.fatigue_score = row.fatigue_score
WITH c, row
MATCH (s:Squadron {sqn_id: row.sqn_id})
MATCH (b:Base {base_id: row.base_id})
MERGE (c)-[:MEMBER_OF]->(s)
MERGE (c)-[:LOCATED_AT]->(b)
"""

_QUALIFIED = """
UNWIND $rows AS row
MATCH (c:Crew {crew_id: row.crew_id})
MERGE (t:AircraftType {type: row.type})
MERGE (c)-[q:QUALIFIED_ON]->(t)
SET q.qualifications = row.qualifications
"""

_AIRSPACE = """
UNWIND $rows AS row
MERGE (z:AirspaceZone {zone_id: row.zone_id})
SET z.name = row.name,
    z.type = row.type,
    z.mission_id = row.mission_id
WITH z, row
MATCH (m:Mission {mission_id: row.mission_id})
MERGE (m)-[:USES_AIRSPACE]->(z)
"""


async def sync_neo4j(
    uri: str,
    user: str,
    password: str,
    targets: list[Target],
    missions: list[Mission],
    threats: list[Threat],
    weapons: list[WeaponType],
    stocks: list[WeaponStock],
    cells: list[WeatherCell],
    bases: list[BaseRecord],
    squadrons: list[Squadron],
    aircraft: list[Aircraft],
    crew: list[Crew],
    zones: list[AirspaceZone],
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
                _weapon_rows(weapons),
                _stock_rows(stocks),
                _base_rows(bases),
                _weather_rows(cells),
                _squadron_rows(squadrons),
                _aircraft_rows(aircraft),
                _crew_rows(crew),
                _qualification_rows(crew),
                _airspace_rows(zones),
            )
    finally:
        await driver.close()


async def _write_graph(
    tx,
    target_rows: list[dict],
    mission_rows: list[dict],
    threat_rows: list[dict],
    cover_rows: list[dict],
    weapon_rows: list[dict],
    stock_rows: list[dict],
    base_rows: list[dict],
    weather_rows: list[dict],
    squadron_rows: list[dict],
    aircraft_rows: list[dict],
    crew_rows: list[dict],
    qualification_rows: list[dict],
    airspace_rows: list[dict],
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
    if weapon_rows:
        await _consume(tx, _WEAPON_TYPES, rows=weapon_rows)
    if stock_rows:
        await _consume(tx, _STOCKS, rows=stock_rows)
    await _write_weather(tx, base_rows, weather_rows)
    if squadron_rows:
        await _consume(tx, _SQUADRONS, rows=squadron_rows)
    if aircraft_rows:
        await _consume(tx, _AIRCRAFT, rows=aircraft_rows)
    if crew_rows:
        await _consume(tx, _CREW, rows=crew_rows)
    if qualification_rows:
        await _consume(tx, _QUALIFIED, rows=qualification_rows)
    if airspace_rows:
        await _consume(tx, _AIRSPACE, rows=airspace_rows)


async def sync_weather_graph(
    uri: str,
    user: str,
    password: str,
    cells: list[WeatherCell],
) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with driver.session() as session:
            await session.execute_write(_write_weather_only, _identity_rows(), _weather_rows(cells))
    finally:
        await driver.close()


async def sync_bases(uri: str, user: str, password: str, bases: list[BaseRecord]) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with driver.session() as session:
            await session.execute_write(_write_bases, _base_rows(bases))
    finally:
        await driver.close()


async def _write_bases(tx, base_rows: list[dict]) -> None:
    if base_rows:
        await _consume(tx, _BASES, rows=base_rows)


async def _write_weather_only(tx, base_rows: list[dict], weather_rows: list[dict]) -> None:
    await _consume(tx, _CLEAR_WEATHER)
    if base_rows:
        await _consume(tx, _BASE_IDENTITY, rows=base_rows)
    if weather_rows:
        await _consume(tx, _WEATHER, rows=weather_rows)


async def _write_weather(tx, base_rows: list[dict], weather_rows: list[dict]) -> None:
    await _consume(tx, _CLEAR_WEATHER)
    if base_rows:
        await _consume(tx, _BASES, rows=base_rows)
    if weather_rows:
        await _consume(tx, _WEATHER, rows=weather_rows)


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


def _weapon_rows(weapons: list[WeaponType]) -> list[dict]:
    return [
        {
            "weapon_code": weapon.weapon_code,
            "category": weapon.category,
            "range_km": weapon.range_km,
            "weight_kg": weapon.weight_kg,
        }
        for weapon in weapons
    ]


def _stock_rows(stocks: list[WeaponStock]) -> list[dict]:
    return [
        {
            "stock_id": stock.stock_id,
            "base_id": stock.base_id,
            "weapon_code": stock.weapon_code,
            "quantity": stock.quantity,
        }
        for stock in stocks
    ]


def _base_rows(bases: list[BaseRecord]) -> list[dict]:
    return [
        {
            "base_id": base.base_id,
            "name": base.name,
            "icao": base.icao,
            "lat": base.lat,
            "lon": base.lon,
            "elevation_ft": base.elevation_ft,
            "runway_count": base.runway_count,
            "runway_length_m": base.runway_length_m,
            "shelter_count": base.shelter_count,
            "fuel_capacity_l": base.fuel_capacity_l,
            "ground_crew_teams": base.ground_crew_teams,
            "max_sorties_per_hour": base.max_sorties_per_hour,
        }
        for base in bases
    ]


def _identity_rows() -> list[dict]:
    return [
        {
            "base_id": airfield.base_id,
            "name": airfield.name,
            "icao": airfield.icao,
            "lat": airfield.lat,
            "lon": airfield.lon,
            "elevation_ft": airfield.elevation_ft,
        }
        for airfield in BASES
    ]


def _weather_rows(cells: list[WeatherCell]) -> list[dict]:
    return [
        {
            "cell_id": f"wx:{airfield.icao}",
            "base_id": airfield.base_id,
            "lat": cell.lat,
            "lon": cell.lon,
            "valid_time": cell.time.isoformat(),
            "ceiling_ft": cell.ceiling_ft,
            "vis_km": cell.visibility_km,
            "wind_kts": cell.wind_kts,
            "cb_flag": cell.cb_flag,
            "flyability": cell.flyability,
            "source": cell.source,
        }
        for airfield, cell in cells_for_bases(cells)
    ]


def _squadron_rows(squadrons: list[Squadron]) -> list[dict]:
    return [
        {
            "sqn_id": squadron.sqn_id,
            "type": squadron.type,
            "name": squadron.name,
            "home_base_id": squadron.home_base_id,
        }
        for squadron in squadrons
    ]


def _aircraft_rows(aircraft: list[Aircraft]) -> list[dict]:
    return [
        {
            "tail_no": airframe.tail_no,
            "type": airframe.type,
            "status": airframe.status,
            "base_id": airframe.base_id,
            "sqn_id": airframe.sqn_id,
            "fuel_kg": airframe.fuel_kg,
        }
        for airframe in aircraft
    ]


def _crew_rows(crew: list[Crew]) -> list[dict]:
    return [
        {
            "crew_id": member.crew_id,
            "role": member.role,
            "callsign": member.callsign,
            "base_id": member.base_id,
            "sqn_id": member.sqn_id,
            "fatigue_score": member.fatigue_score,
        }
        for member in crew
    ]


def _qualification_rows(crew: list[Crew]) -> list[dict]:
    return [
        {
            "crew_id": member.crew_id,
            "type": aircraft_type,
            "qualifications": qualifications,
        }
        for member in crew
        for aircraft_type, qualifications in sorted(member.qualifications.items())
    ]


def _airspace_rows(zones: list[AirspaceZone]) -> list[dict]:
    return [
        {
            "zone_id": zone.zone_id,
            "name": zone.name,
            "type": zone.type,
            "mission_id": zone.mission_id,
        }
        for zone in zones
        if zone.mission_id is not None
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
