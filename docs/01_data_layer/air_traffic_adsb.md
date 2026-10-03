# Air Traffic (ADS-B)

## Purpose
Real civil traffic for deconfliction of routes with commercial corridors, and a realistic background picture in the demo.

## Source
OpenSky Network REST API `GET /api/states/all?lamin=..&lomin=..&lamax=..&lomax=..` (anonymous: ~10s resolution limits, rate-limited; use OAuth account for more). Fields: icao24, callsign, origin_country, time_position, lon, lat, baro_altitude, velocity, true_track, vertical_rate, on_ground.

Note: coverage over some areas is sparse; keep a recorded sample file for offline demo.

## Schema
`adsb_track(time, icao24, callsign, lat, lon, geom, alt_m, speed_ms, heading, vrate, on_ground)` (TimescaleDB hypertable, 24 h retention, downsampled aggregate to 5-min).

## Derived
- Civil density grid (flights per cell per hour) used as soft cost in routing.
- Conflict alert: military route within 10 km / 1000 ft of a civil track within +-5 min.

## Storage
TimescaleDB for history; Redis geo set `adsb:live` with TTL 120 s; Kafka `garuda.adsb.positions`; MinIO raw snapshots.

## Ingestion
Poll every 15-30 s for the theatre bbox; backoff on HTTP 429.

## Consumers
`cpsat_airspace_deconfliction`, route planning, harness `get_civil_traffic`.
