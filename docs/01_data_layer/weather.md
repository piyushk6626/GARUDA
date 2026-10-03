# Weather

## Purpose
Forecast and observed weather affecting launch, route, target area, and recovery.

## Sources (real)
- **Open-Meteo** forecast API (free, no key): hourly cloud cover (low/mid/high), visibility, wind speed/direction at 10m and 850/700/500 hPa, gusts, precipitation, CAPE, temperature, freezing level. Call on a grid (0.5 deg) across the theatre.
- **aviationweather.gov** METAR/TAF for Indian ICAO stations (VIJO, VIJR, VIBK, VIAM, VIAX, VABJ, VIAG...). JSON endpoint `/api/data/metar?ids=...&format=json`.
- Optional: IMD data for validation.

## Schema
`weather_grid` (forecast cells):
| Field | Type |
|---|---|
| time | timestamptz |
| lat, lon | real (grid centre) |
| cloud_low/mid/high_pct | real |
| ceiling_ft | real (derived) |
| visibility_km | real |
| wind_kts, wind_dir_deg, gust_kts | real |
| precip_mm_h | real |
| cape_jkg | real |
| temp_c, freezing_level_ft | real |
| source, issued_at | text, timestamptz |

`weather_station_obs`: `icao, obs_time, raw_text, flight_category (VFR/MVFR/IFR/LIFR), ceiling_ft, vis_km, wind_kts, crosswind_kts`.

## Derived flyability index
Per base and per route cell: `flyability = min(f_ceiling, f_vis, f_wind, f_cb, f_icing)` each in [0,1] from piecewise-linear thresholds (e.g. vis < 1.5 km for takeoff => 0). Also `cb_flag = CAPE > 1000`. Stored with the cell.

## Storage
- TimescaleDB hypertable `weather_grid` partitioned by time, spatial index on (lat, lon) or `geometry` point; retention: forecasts 7 days, observations 30 days.
- Redis: `wx:base:{icao}` JSON for latest + flyability; `wx:grid:{valid_hour}` for quick route lookups.
- MinIO: raw API JSON `raw/weather/{date}/`.
- Kafka: `garuda.weather.update`.
- Neo4j: `(:WeatherCell)-[:AFFECTS]->(:Base)` only for bases.

## Ingestion
Scheduler (APScheduler) every 60 min for Open-Meteo, 30 min for METAR. Cache and fallback to last good record; fixture file for offline demos. Synthetic override to inject "fog at B5".

## Consumers
Weather impact model, route planner (cost), MILP (base closures), harness `get_weather_along_route`.
