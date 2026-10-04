import json
from datetime import datetime, timezone

from redis.asyncio import Redis

from garuda.generators.weather import representative_observations
from garuda.queries.adsb import latest_tracks
from garuda.schemas.adsb import AdsbTrack
from garuda.schemas.aircraft import Aircraft
from garuda.schemas.airspace import AirspaceZone
from garuda.schemas.crew import Crew
from garuda.schemas.gis import BaseRecord
from garuda.schemas.mission import GeneratedScenario
from garuda.schemas.weapon import WeaponStock
from garuda.schemas.weather import WeatherCell, WeatherStationObs


ADSB_TTL_S = 120


async def _stale(client: Redis, pattern: str) -> list:
    return [key async for key in client.scan_iter(match=pattern, count=500)]


async def seed_redis(url: str, scenario: GeneratedScenario) -> tuple[int, int, int]:
    client = Redis.from_url(url)
    try:
        pending = {m.mission_id: m.priority for m in scenario.missions if m.status == "REQUESTED"}
        active = [t for t in scenario.threats if t.status == "ACTIVE"]
        places: list[float | str] = [v for t in active for v in (t.lon, t.lat, t.threat_id)]
        stale = await _stale(client, "stock:*")
        pipe = client.pipeline()
        pipe.delete("missions:pending", "threat:active", *stale)
        if pending:
            pipe.zadd("missions:pending", pending)
        if places:
            pipe.geoadd("threat:active", places)
        for stock in scenario.stocks:
            pipe.set(_stock_key(stock), stock.quantity)
        await pipe.execute()
        await write_weather_cache(client, scenario.cells, scenario.observations)
        await write_base_cache(client, scenario.bases)
        await write_aircraft_cache(client, scenario.aircraft)
        await write_crew_cache(client, scenario.crew)
        await write_airspace_cache(client, scenario.zones)
        await write_adsb_cache(client, scenario.adsb_tracks)
        return len(pending), len(active), len(scenario.stocks)
    finally:
        await client.aclose()


def _stock_key(stock: WeaponStock) -> str:
    return f"stock:{stock.base_id}:{stock.weapon_code}"


async def write_weather_cache(
    client: Redis,
    cells: list[WeatherCell],
    observations: list[WeatherStationObs],
) -> None:
    stale_grid = await _stale(client, "wx:grid:*")
    stale_base = await _stale(client, "wx:base:*")
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for cell in cells:
        hour = _hour_stamp(cell.time)
        grouped.setdefault(hour, {})[f"{cell.lat:.1f},{cell.lon:.1f}"] = {
            "flyability": cell.flyability,
            "ceiling_ft": cell.ceiling_ft,
            "vis_km": cell.visibility_km,
            "wind_kts": cell.wind_kts,
            "cb_flag": cell.cb_flag,
        }
    pipe = client.pipeline()
    if stale_grid:
        pipe.delete(*stale_grid)
    if stale_base:
        pipe.delete(*stale_base)
    for hour, payload in grouped.items():
        pipe.set(f"wx:grid:{hour}", json.dumps(payload))
    for obs in representative_observations(observations):
        pipe.set(f"wx:base:{obs.icao}", json.dumps(obs.model_dump(mode="json")))
    await pipe.execute()


def _hour_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def write_base_cache(client: Redis, bases: list[BaseRecord]) -> None:
    stale: list[str | bytes] = []
    async for key in client.scan_iter(match="base:*", count=100):
        text = key.decode() if isinstance(key, bytes) else key
        if text.count(":") == 1:
            stale.append(key)
    pipe = client.pipeline()
    if stale:
        pipe.delete(*stale)
    for base in bases:
        pipe.hset(f"base:{base.base_id}", mapping=_base_hash(base))
    await pipe.execute()


async def write_aircraft_cache(client: Redis, aircraft: list[Aircraft]) -> None:
    stale_hashes = await _stale(client, "ac:*")
    stale_sets = await _stale(client, "base:*:fmc")
    grouped: dict[str, list[str]] = {}
    for airframe in aircraft:
        if airframe.status == "FMC":
            grouped.setdefault(airframe.base_id, []).append(airframe.tail_no)
    pipe = client.pipeline()
    if stale_hashes:
        pipe.delete(*stale_hashes)
    if stale_sets:
        pipe.delete(*stale_sets)
    for airframe in aircraft:
        pipe.hset(f"ac:{airframe.tail_no}", mapping=_aircraft_hash(airframe))
    for base_id, tails in grouped.items():
        pipe.sadd(f"base:{base_id}:fmc", *tails)
    await pipe.execute()


async def write_crew_cache(client: Redis, crew: list[Crew]) -> None:
    stale = await _stale(client, "crew:*")
    pipe = client.pipeline()
    if stale:
        pipe.delete(*stale)
    for member in crew:
        pipe.hset(f"crew:{member.crew_id}", mapping=_crew_hash(member))
    await pipe.execute()


async def write_airspace_cache(client: Redis, zones: list[AirspaceZone]) -> None:
    active = [zone.zone_id for zone in zones if _zone_active_at_seed(zone)]
    pipe = client.pipeline()
    pipe.delete("airspace:active")
    if active:
        pipe.sadd("airspace:active", *active)
    await pipe.execute()


async def write_adsb_cache(client: Redis, tracks: list[AdsbTrack]) -> None:
    stale = await _stale(client, "adsb:track:*")
    places: list[float | str] = []
    latest = latest_tracks(tracks)
    for track in latest:
        places.extend((track.lon, track.lat, track.icao24))
    pipe = client.pipeline()
    pipe.delete("adsb:live")
    if stale:
        pipe.delete(*stale)
    if places:
        pipe.geoadd("adsb:live", places)
        pipe.expire("adsb:live", ADSB_TTL_S)
    for track in latest:
        pipe.set(
            f"adsb:track:{track.icao24}",
            json.dumps(track.model_dump(mode="json")),
            ex=ADSB_TTL_S,
        )
    await pipe.execute()


def _zone_active_at_seed(zone: AirspaceZone) -> bool:
    if zone.active_from is None or zone.active_to is None:
        return True
    return False


def _aircraft_hash(airframe: Aircraft) -> dict[str, str]:
    return {
        "tail_no": airframe.tail_no,
        "type": airframe.type,
        "status": airframe.status,
        "base_id": airframe.base_id,
        "fuel_kg": f"{airframe.fuel_kg:.1f}",
        "updated_at": airframe.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _crew_hash(member: Crew) -> dict[str, str]:
    return {
        "crew_id": member.crew_id,
        "role": member.role,
        "base_id": member.base_id,
        "sqn_id": member.sqn_id,
        "medical_status": member.medical_status,
        "fatigue_score": f"{member.fatigue_score:.4f}",
        "duty_hours_24h": f"{member.duty_hours_24h:.1f}",
    }


def _base_hash(base: BaseRecord) -> dict[str, str]:
    return {
        "base_id": base.base_id,
        "name": base.name,
        "icao": base.icao,
        "lat": str(base.lat),
        "lon": str(base.lon),
        "elevation_ft": str(base.elevation_ft),
        "runway_count": str(base.runway_count),
        "runway_length_m": str(base.runway_length_m),
        "shelter_count": str(base.shelter_count),
        "fuel_capacity_l": str(base.fuel_capacity_l),
        "ground_crew_teams": str(base.ground_crew_teams),
        "max_sorties_per_hour": str(base.max_sorties_per_hour),
    }
