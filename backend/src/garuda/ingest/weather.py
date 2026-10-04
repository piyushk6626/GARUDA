"""Fetch Open-Meteo and METAR into the weather tables.

The simulator keeps using the synthetic series. This command upserts live rows
and does not apply the fog override or truncate the rest of the scenario.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from garuda.bases import BASES, base_by_icao
from garuda.config import BoundingBox
from garuda.generators.weather import (
    crosswind_kts,
    flight_category,
    flyability,
    grid_points,
    metres_to_feet,
    snap_half,
    wind_dir,
)
from garuda.generators.weather import _cell as forecast_cell
from garuda.schemas.weather import FlightCategory, WeatherCell, WeatherStationObs
from garuda.storage.kafka_publish import publish_weather
from garuda.storage.neo4j_sync import sync_weather_graph
from garuda.storage.postgres import upsert_weather
from garuda.storage.redis_seed import write_weather_cache
from garuda.storage.seed import kafka_bootstrap, neo4j_settings, postgres_dsn, redis_url

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
METAR_URL = "https://aviationweather.gov/api/data/metar"
HOURLY_FIELDS = (
    "cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,"
    "cape,temperature_2m,freezing_level_height"
)
BATCH_SIZE = 40
_SM_TO_KM = 1.60934
_COVER_PCT = {"SKC": 0, "CLR": 0, "NSC": 0, "FEW": 25, "SCT": 50, "BKN": 75, "OVC": 100, "VV": 100}
FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "weather"
SAMPLE_GRID = FIXTURE_DIR / "open_meteo_sample.json"
SAMPLE_METAR = FIXTURE_DIR / "metar_sample.json"
LAST_GRID = FIXTURE_DIR / "last_good_open_meteo.json"
LAST_METAR = FIXTURE_DIR / "last_good_metar.json"


@dataclass(frozen=True)
class IngestResult:
    cells: int
    observations: int
    live: bool
    events: int


def map_open_meteo(payload: object, issued_at: datetime) -> list[WeatherCell]:
    blocks = payload if isinstance(payload, list) else [payload]
    issued = _as_utc(issued_at)
    cells: list[WeatherCell] = []
    for block in blocks:
        if not isinstance(block, dict) or "hourly" not in block:
            continue
        hourly = block["hourly"]
        if not isinstance(hourly, dict):
            continue
        times = hourly.get("time") or []
        lat = snap_half(float(block["latitude"]))
        lon = snap_half(float(block["longitude"]))
        for index, stamp in enumerate(times):
            cell = _open_meteo_cell(hourly, index, lat, lon, _parse_time(stamp), issued)
            if cell is not None:
                cells.append(cell)
    return cells


def map_metar(payload: object) -> list[WeatherStationObs]:
    reports = payload if isinstance(payload, list) else [payload]
    observations: list[WeatherStationObs] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        obs = _metar_obs(report)
        if obs is not None:
            observations.append(obs)
    return observations


async def ingest_weather() -> IngestResult:
    grid_live, grid_payload = await _load("grid")
    metar_live, metar_payload = await _load("metar")
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    cells = map_open_meteo(grid_payload, issued_at)
    observations = map_metar(metar_payload)
    await upsert_weather(postgres_dsn(), cells, observations)
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url())
    try:
        await write_weather_cache(client, cells, observations)
    finally:
        await client.aclose()
    events = await publish_weather(kafka_bootstrap(), cells, observations)
    uri, user, password = neo4j_settings()
    await sync_weather_graph(uri, user, password, cells)
    return IngestResult(
        cells=len(cells),
        observations=len(observations),
        live=grid_live and metar_live,
        events=events,
    )


def main() -> None:
    result = asyncio.run(ingest_weather())
    mode = "live" if result.live else "fixture"
    print(
        f"weather {mode} cells={result.cells} observations={result.observations} events={result.events}"
    )


async def _load(kind: str) -> tuple[bool, object]:
    last = LAST_GRID if kind == "grid" else LAST_METAR
    sample = SAMPLE_GRID if kind == "grid" else SAMPLE_METAR
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = await fetch_open_meteo(client) if kind == "grid" else await fetch_metar(client)
        _write_json(last, payload)
        return True, payload
    except (httpx.HTTPError, OSError, ValueError):
        return False, json.loads(_prefer(last, sample).read_text())


async def fetch_open_meteo(client: httpx.AsyncClient) -> list[dict]:
    points = grid_points(BoundingBox())
    blocks: list[dict] = []
    for offset in range(0, len(points), BATCH_SIZE):
        chunk = points[offset : offset + BATCH_SIZE]
        response = await client.get(
            OPEN_METEO_URL,
            params={
                "latitude": ",".join(f"{lat:.1f}" for lat, _lon in chunk),
                "longitude": ",".join(f"{lon:.1f}" for _lat, lon in chunk),
                "hourly": HOURLY_FIELDS,
                "wind_speed_unit": "kn",
                "timezone": "UTC",
                "forecast_hours": 72,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            blocks.append(payload)
            continue
        if isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
            continue
        raise ValueError("open-meteo payload must be an object or a list")
    return blocks


async def fetch_metar(client: httpx.AsyncClient) -> object:
    response = await client.get(
        METAR_URL,
        params={"ids": ",".join(airfield.icao for airfield in BASES), "format": "json"},
    )
    response.raise_for_status()
    return response.json()


def _open_meteo_cell(
    hourly: dict,
    index: int,
    lat: float,
    lon: float,
    moment: datetime,
    issued_at: datetime,
) -> WeatherCell | None:
    visibility = _series(hourly, "visibility", index)
    wind = _series(hourly, "wind_speed_10m", index)
    direction = _series(hourly, "wind_direction_10m", index)
    temp = _series(hourly, "temperature_2m", index)
    if None in {visibility, wind, direction, temp}:
        return None
    gust = _series(hourly, "wind_gusts_10m", index)
    if gust is None:
        gust = wind
    freezing = _series(hourly, "freezing_level_height", index)
    return forecast_cell(
        time=moment,
        lat=lat,
        lon=lon,
        cloud_low=_or_zero(_series(hourly, "cloud_cover_low", index)),
        cloud_mid=_or_zero(_series(hourly, "cloud_cover_mid", index)),
        cloud_high=_or_zero(_series(hourly, "cloud_cover_high", index)),
        visibility=float(visibility) / 1000,
        wind=float(wind),
        gust=float(gust),
        wind_direction=float(direction),
        precip=_or_zero(_series(hourly, "precipitation", index)),
        cape=_or_zero(_series(hourly, "cape", index)),
        temp=float(temp),
        freezing=0.0 if freezing is None else metres_to_feet(float(freezing)),
        issued_at=issued_at,
        source="open-meteo",
    )


def _metar_obs(report: dict) -> WeatherStationObs | None:
    airfield = base_by_icao(str(report.get("icaoId") or ""))
    if airfield is None:
        return None
    raw_text = str(report.get("rawOb") or "")
    vis = _statute_to_km(report.get("visib"))
    clouds = report.get("clouds") if isinstance(report.get("clouds"), list) else []
    height = _metar_ceiling(clouds)
    direction = 0.0 if report.get("wdir") in {None, "VRB"} else wind_dir(float(report["wdir"]))
    speed = float(report.get("wspd") or 0)
    cross = crosswind_kts(speed, direction, airfield.runway_true_deg)
    temp = report.get("temp")
    temp_c = 20.0 if temp is None else float(temp)
    cloud_pct = _cloud_pct(clouds)
    has_cb = _has_cb(report, clouds)
    category = _category(report.get("fltCat"), height, vis)
    return WeatherStationObs(
        icao=airfield.icao,
        obs_time=_parse_time(report.get("reportTime") or report.get("obsTime")),
        raw_text=raw_text,
        flight_category=category,
        ceiling_ft=height,
        vis_km=vis,
        wind_kts=speed,
        crosswind_kts=cross,
        flyability=flyability(
            visibility_km=vis,
            ceiling=height,
            wind_kts=cross,
            cape_jkg=2500.0 if has_cb else 0.0,
            temp_c=temp_c,
            cloud_low_pct=cloud_pct,
            cloud_mid_pct=0,
            use_crosswind=True,
        ),
        source="metar",
    )


def _series(hourly: dict, name: str, index: int) -> float | None:
    values = hourly.get(name)
    if not isinstance(values, list) or index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def _or_zero(value: float | None) -> float:
    return 0.0 if value is None else value


def _statute_to_km(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value) * _SM_TO_KM
    text = str(value).strip().upper().rstrip("+")
    if " " in text and "/" in text:
        whole, fraction = text.split(maxsplit=1)
        numerator, denominator = fraction.split("/")
        miles = float(whole) + float(numerator) / float(denominator)
        return miles * _SM_TO_KM
    if "/" in text:
        numerator, denominator = text.split("/")
        return (float(numerator) / float(denominator)) * _SM_TO_KM
    return float(text) * _SM_TO_KM


def _metar_ceiling(clouds: list) -> float | None:
    bases = [
        float(cloud["base"])
        for cloud in clouds
        if isinstance(cloud, dict)
        and str(cloud.get("cover") or "") in {"BKN", "OVC", "VV"}
        and cloud.get("base") is not None
    ]
    if not bases:
        return None
    return min(bases)


def _cloud_pct(clouds: list) -> float:
    amounts = [
        _COVER_PCT.get(str(cloud.get("cover") or ""), 0)
        for cloud in clouds
        if isinstance(cloud, dict)
    ]
    return float(max(amounts, default=0))


def _has_cb(report: dict, clouds: list) -> bool:
    raw = f"{report.get('rawOb') or ''} {report.get('wxString') or ''}"
    if "CB" in raw:
        return True
    return any("CB" in f"{cloud.get('cover') or ''} {cloud.get('type') or ''}" for cloud in clouds if isinstance(cloud, dict))


def _category(reported: object, height: float | None, visibility_km: float) -> FlightCategory:
    if reported in {"VFR", "MVFR", "IFR", "LIFR"}:
        return reported  # type: ignore[return-value]
    return flight_category(height, visibility_km)


def _parse_time(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    text = str(value).replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _prefer(last: Path, sample: Path) -> Path:
    return last if last.is_file() else sample


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
