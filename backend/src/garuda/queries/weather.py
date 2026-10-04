"""Route weather lookup over an in-memory forecast grid."""

import math
from datetime import datetime, timedelta, timezone

from garuda.generators.weather import snap_half
from garuda.schemas.weather import WeatherCell, WeatherRouteQuery, WeatherRouteResult, WeatherSegment

SAMPLE_KM = 25.0
_KM_PER_NM = 1.852
_ICING_DEPTH_FT = 4000
_ADVISORIES = (
    ("visibility", "visibility below takeoff minimum"),
    ("ceiling", "ceiling below minimum"),
    ("cb", "cumulonimbus along route"),
    ("icing", "icing along route"),
)


def get_weather_along_route(cells: list[WeatherCell], query: WeatherRouteQuery) -> WeatherRouteResult:
    if query.speed_kts <= 0:
        raise ValueError("speed_kts must be positive")
    if not query.route:
        raise ValueError("route needs at least one point")

    index = {
        (round(cell.lat, 1), round(cell.lon, 1), _as_utc(cell.time)): cell
        for cell in cells
    }
    flags = {name: False for name, _text in _ADVISORIES}
    segments: list[WeatherSegment] = []
    for lat, lon, distance_km in _samples(query.route):
        elapsed = timedelta(hours=(distance_km / _KM_PER_NM) / query.speed_kts)
        moment = _nearest_hour(_as_utc(query.start_time) + elapsed)
        key = (snap_half(lat), snap_half(lon), moment)
        cell = index.get(key)
        if cell is None:
            stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            raise ValueError(f"no forecast for {key[0]:.1f},{key[1]:.1f} at {stamp}")
        score, icing = _at_altitude(cell, query.altitude_ft)
        segments.append(
            WeatherSegment(
                time=moment,
                lat=lat,
                lon=lon,
                flyability=score,
                ceiling_ft=cell.ceiling_ft,
                vis_km=cell.visibility_km,
                wind_kts=cell.wind_kts,
                wind_dir_deg=cell.wind_dir_deg,
                cb_flag=cell.cb_flag,
            )
        )
        if cell.visibility_km <= 1.5:
            flags["visibility"] = True
        if cell.ceiling_ft is not None and cell.ceiling_ft < 3000:
            flags["ceiling"] = True
        if cell.cb_flag:
            flags["cb"] = True
        if icing:
            flags["icing"] = True

    return WeatherRouteResult(
        segments=segments,
        worst_flyability=min(segment.flyability for segment in segments),
        advisories=[text for name, text in _ADVISORIES if flags[name]],
    )


def _samples(route: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    points = [(route[0][0], route[0][1], 0.0)]
    travelled = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(route, route[1:]):
        leg = _haversine_km(lat1, lon1, lat2, lon2)
        if leg == 0:
            continue
        placed = 0.0
        while placed + SAMPLE_KM < leg:
            placed += SAMPLE_KM
            fraction = placed / leg
            points.append(
                (
                    lat1 + (lat2 - lat1) * fraction,
                    lon1 + (lon2 - lon1) * fraction,
                    travelled + placed,
                )
            )
        travelled += leg
        points.append((lat2, lon2, travelled))
    return points


def _at_altitude(cell: WeatherCell, altitude_ft: int) -> tuple[float, bool]:
    cloud = cell.cloud_low_pct + cell.cloud_mid_pct + cell.cloud_high_pct
    in_layer = cell.freezing_level_ft <= altitude_ft <= cell.freezing_level_ft + _ICING_DEPTH_FT
    icing = in_layer and cloud >= 50
    if not icing:
        return cell.flyability, False
    return min(cell.flyability, 0.0), True


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    chord = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(chord))


def _nearest_hour(moment: datetime) -> datetime:
    moment = _as_utc(moment)
    base = moment.replace(minute=0, second=0, microsecond=0)
    if moment - base >= timedelta(minutes=30):
        return base + timedelta(hours=1)
    return base


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
