"""Synthetic theatre weather for the simulator.

The field is a smooth desert VMC pattern. The scenario fog event is applied
after the field so B5 can close without changing the rest of the draw.
Real Open-Meteo and METAR rows use the same flyability helpers.
"""

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from garuda.common import as_utc as _as_utc
from garuda.bases import BASES, Airfield, base_by_id
from garuda.config import BoundingBox, ScenarioConfig
from garuda.schemas.weather import FlightCategory, WeatherCell, WeatherSource, WeatherStationObs

GRID_STEP = 0.5
FOG_HOURS = 6
FOG_VIS_KM = 0.4
SOURCE_SYNTHETIC = "synthetic"
_METERS_TO_FEET = 3.28084


def snap_half(value: float) -> float:
    return round(value * 2) / 2


def grid_axis(start: float, stop: float, step: float = GRID_STEP) -> list[float]:
    count = int(round((stop - start) / step))
    return [round(start + index * step, 1) for index in range(count + 1)]


def grid_points(bbox: BoundingBox) -> list[tuple[float, float]]:
    lats = grid_axis(bbox.lat_min, bbox.lat_max)
    lons = grid_axis(bbox.lon_min, bbox.lon_max)
    return [(lat, lon) for lat in lats for lon in lons]


def crosswind_kts(wind_kts: float, wind_dir_deg: float, runway_true_deg: float) -> float:
    delta = math.radians(wind_dir_deg - runway_true_deg)
    return abs(wind_kts * math.sin(delta))


def ceiling_ft(cloud_low_pct: float) -> float | None:
    if cloud_low_pct < 50:
        return None
    fraction = (cloud_low_pct - 50) / 50
    return 8000 - fraction * (8000 - 200)


def flight_category(ceiling: float | None, visibility_km: float) -> FlightCategory:
    height = 99_999.0 if ceiling is None else ceiling
    if height < 500 or visibility_km < 1.5:
        return "LIFR"
    if height < 1000 or visibility_km < 5:
        return "IFR"
    if height < 3000 or visibility_km < 8:
        return "MVFR"
    return "VFR"


def flyability(
    *,
    visibility_km: float,
    ceiling: float | None,
    wind_kts: float,
    cape_jkg: float,
    temp_c: float,
    cloud_low_pct: float,
    cloud_mid_pct: float,
    use_crosswind: bool = False,
) -> float:
    factors = (
        _score(visibility_km, zero_at=1.5, one_at=8),
        1.0 if ceiling is None else _score(ceiling, zero_at=200, one_at=3000),
        _score(wind_kts, zero_at=25, one_at=10) if use_crosswind else _score(wind_kts, zero_at=35, one_at=15),
        _score(cape_jkg, zero_at=2500, one_at=1000),
        _icing(temp_c, cloud_low_pct + cloud_mid_pct),
    )
    return min(factors)


def metres_to_feet(metres: float) -> float:
    return metres * _METERS_TO_FEET


def wind_dir(value: float) -> float:
    direction = value % 360
    if direction < 0:
        direction += 360
    return 0.0 if direction == 360 else direction


def build_weather(
    config: ScenarioConfig,
    rng: np.random.Generator,
) -> tuple[list[WeatherCell], list[WeatherStationObs]]:
    start = _as_utc(config.start)
    lats = grid_axis(config.bbox.lat_min, config.bbox.lat_max)
    lons = grid_axis(config.bbox.lon_min, config.bbox.lon_max)
    spatial = _spatial(lats, lons, rng)
    cells = _cells(config, start, lats, lons, spatial)
    _apply_fog(config, start, cells)
    return cells, _observations(cells)


def representative_observations(observations: list[WeatherStationObs]) -> list[WeatherStationObs]:
    chosen: dict[str, WeatherStationObs] = {}
    for obs in observations:
        current = chosen.get(obs.icao)
        if current is None or _as_utc(obs.obs_time) < _as_utc(current.obs_time):
            chosen[obs.icao] = obs
    return [chosen[icao] for icao in sorted(chosen)]


def cells_for_bases(cells: list[WeatherCell]) -> list[tuple[Airfield, WeatherCell]]:
    if not cells:
        return []
    moment = min(_as_utc(cell.time) for cell in cells)
    index = {(round(cell.lat, 1), round(cell.lon, 1), _as_utc(cell.time)): cell for cell in cells}
    chosen: list[tuple[Airfield, WeatherCell]] = []
    for airfield in BASES:
        cell = index.get((snap_half(airfield.lat), snap_half(airfield.lon), moment))
        if cell is not None:
            chosen.append((airfield, cell))
    return chosen


def _score(value: float, zero_at: float, one_at: float) -> float:
    if zero_at == one_at:
        return 1.0 if value == one_at else 0.0
    score = (value - zero_at) / (one_at - zero_at)
    return min(1.0, max(0.0, score))


def _icing(temp_c: float, cloud_pct: float) -> float:
    if temp_c < -12 or temp_c > 0:
        return 1.0
    if cloud_pct >= 50:
        return 0.0
    if cloud_pct < 20:
        return 1.0
    return (50 - cloud_pct) / 30


def _spatial(lats: list[float], lons: list[float], rng: np.random.Generator) -> np.ndarray:
    phase = rng.random(2)
    lat_span = max(lats[-1] - lats[0], 1.0)
    lon_span = max(lons[-1] - lons[0], 1.0)
    lat_n = (np.asarray(lats) - lats[0]) / lat_span
    lon_n = (np.asarray(lons) - lons[0]) / lon_span
    yy, xx = np.meshgrid(lat_n, lon_n, indexing="ij")
    wave = np.sin(2 * np.pi * (yy + phase[0])) * np.cos(2 * np.pi * (xx + phase[1]))
    return 0.5 + 0.5 * wave


def _cells(
    config: ScenarioConfig,
    start: datetime,
    lats: list[float],
    lons: list[float],
    spatial: np.ndarray,
) -> list[WeatherCell]:
    cells: list[WeatherCell] = []
    for hour in range(config.horizon_hours):
        moment = start + timedelta(hours=hour)
        hour_utc = moment.hour
        afternoon = math.exp(-((hour_utc - 12) ** 2) / 18)
        warm = math.sin(2 * math.pi * (hour_utc - 9) / 24)
        for lat_index, lat in enumerate(lats):
            for lon_index, lon in enumerate(lons):
                place = float(spatial[lat_index, lon_index])
                cloud_low = 10 + 15 * place
                cloud_mid = 5 + 20 * afternoon * place
                cloud_high = 5 + 10 * place
                cape = 200 + 1800 * afternoon * place
                wind = 8 + 4 * place
                cells.append(
                    _cell(
                        time=moment,
                        lat=lat,
                        lon=lon,
                        cloud_low=cloud_low,
                        cloud_mid=cloud_mid,
                        cloud_high=cloud_high,
                        visibility=12 + 2 * place,
                        wind=wind,
                        gust=wind + 1,
                        wind_direction=wind_dir(220 + 40 * place),
                        precip=0.2 * afternoon * place,
                        cape=cape,
                        temp=26 + 8 * warm + 2 * place,
                        freezing=14000 + 1500 * place,
                        issued_at=start,
                    )
                )
    return cells


def _cell(
    *,
    time: datetime,
    lat: float,
    lon: float,
    cloud_low: float,
    cloud_mid: float,
    cloud_high: float,
    visibility: float,
    wind: float,
    gust: float,
    wind_direction: float,
    precip: float,
    cape: float,
    temp: float,
    freezing: float,
    issued_at: datetime,
    source: WeatherSource = SOURCE_SYNTHETIC,
) -> WeatherCell:
    low = _clamp(cloud_low, 0, 100)
    mid = _clamp(cloud_mid, 0, 100)
    high = _clamp(cloud_high, 0, 100)
    height = ceiling_ft(low)
    return WeatherCell(
        time=time,
        lat=lat,
        lon=lon,
        cloud_low_pct=low,
        cloud_mid_pct=mid,
        cloud_high_pct=high,
        ceiling_ft=height,
        visibility_km=max(0.0, visibility),
        wind_kts=max(0.0, wind),
        wind_dir_deg=wind_dir(wind_direction),
        gust_kts=max(0.0, gust),
        precip_mm_h=max(0.0, precip),
        cape_jkg=max(0.0, cape),
        temp_c=temp,
        freezing_level_ft=max(0.0, freezing),
        source=source,
        issued_at=issued_at,
        flyability=flyability(
            visibility_km=max(0.0, visibility),
            ceiling=height,
            wind_kts=max(wind, gust, 0.0),
            cape_jkg=max(0.0, cape),
            temp_c=temp,
            cloud_low_pct=low,
            cloud_mid_pct=mid,
        ),
        cb_flag=cape > 1000,
    )


def _apply_fog(config: ScenarioConfig, start: datetime, cells: list[WeatherCell]) -> None:
    windows = _fog_windows(config, start)
    if not windows:
        return
    for index, cell in enumerate(cells):
        for airfield, begin, end in windows:
            same_place = snap_half(airfield.lat) == round(cell.lat, 1) and snap_half(airfield.lon) == round(cell.lon, 1)
            if same_place and begin <= _as_utc(cell.time) < end:
                cells[index] = _fog_cell(cell)
                break


def _fog_windows(config: ScenarioConfig, start: datetime) -> list[tuple[Airfield, datetime, datetime]]:
    windows: list[tuple[Airfield, datetime, datetime]] = []
    for event in config.events:
        if event.type != "base_closed" or event.reason != "fog":
            continue
        airfield = base_by_id(event.base or "B5")
        begin = start + timedelta(hours=event.t)
        windows.append((airfield, begin, begin + timedelta(hours=FOG_HOURS)))
    return windows


def _fog_cell(cell: WeatherCell) -> WeatherCell:
    return _cell(
        time=cell.time,
        lat=cell.lat,
        lon=cell.lon,
        cloud_low=100,
        cloud_mid=cell.cloud_mid_pct,
        cloud_high=cell.cloud_high_pct,
        visibility=FOG_VIS_KM,
        wind=cell.wind_kts,
        gust=cell.gust_kts,
        wind_direction=cell.wind_dir_deg,
        precip=0,
        cape=cell.cape_jkg,
        temp=cell.temp_c,
        freezing=cell.freezing_level_ft,
        issued_at=cell.issued_at,
        source=cell.source,
    )


def _observations(cells: list[WeatherCell]) -> list[WeatherStationObs]:
    by_place: dict[tuple[float, float], list[WeatherCell]] = {}
    for cell in cells:
        by_place.setdefault((round(cell.lat, 1), round(cell.lon, 1)), []).append(cell)
    observations: list[WeatherStationObs] = []
    for airfield in BASES:
        place = by_place.get((snap_half(airfield.lat), snap_half(airfield.lon)), [])
        for cell in sorted(place, key=lambda item: item.time):
            observations.append(_observation(airfield, cell, fog=cell.visibility_km <= FOG_VIS_KM))
    return observations


def _observation(airfield: Airfield, cell: WeatherCell, fog: bool) -> WeatherStationObs:
    cross = crosswind_kts(cell.wind_kts, cell.wind_dir_deg, airfield.runway_true_deg)
    return WeatherStationObs(
        icao=airfield.icao,
        obs_time=cell.time,
        raw_text=_metar(airfield.icao, _as_utc(cell.time), cell, fog),
        flight_category=flight_category(cell.ceiling_ft, cell.visibility_km),
        ceiling_ft=cell.ceiling_ft,
        vis_km=cell.visibility_km,
        wind_kts=cell.wind_kts,
        crosswind_kts=cross,
        flyability=flyability(
            visibility_km=cell.visibility_km,
            ceiling=cell.ceiling_ft,
            wind_kts=cross,
            cape_jkg=cell.cape_jkg,
            temp_c=cell.temp_c,
            cloud_low_pct=cell.cloud_low_pct,
            cloud_mid_pct=cell.cloud_mid_pct,
            use_crosswind=True,
        ),
        source=cell.source,
    )


def _metar(icao: str, moment: datetime, cell: WeatherCell, fog: bool) -> str:
    direction = int(round(cell.wind_dir_deg / 10.0) * 10) % 360
    speed = int(round(cell.wind_kts))
    vis = "9999" if cell.visibility_km >= 10 else f"{int(round(cell.visibility_km * 1000)):04d}"
    weather = " FG" if fog else ""
    clouds = "NSC" if cell.ceiling_ft is None else f"OVC{int(round(cell.ceiling_ft / 100)):03d}"
    temp = int(round(cell.temp_c))
    dew = temp - 2
    return (
        f"METAR {icao} {moment.strftime('%d%H%M')}Z "
        f"{direction:03d}{speed:02d}KT {vis}{weather} {clouds} {temp:02d}/{dew:02d} Q1012"
    )


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
