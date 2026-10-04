# Airspace

## Purpose
Structure the sky: where flight is prohibited, restricted, reserved for missions, or civil-controlled, with altitude and time bounds.

## Source
- **Real**: OpenAIP (airspaces, airways, airports for India; free API key, GeoJSON/openAIP format).
- **Synthetic**: military zones (CAP orbits, tanker tracks, ingress/egress corridors, TRAs, missile-engagement zones, ADIZ polygon).

## Schema (`airspace_zone`)
| Field | Type | Notes |
|---|---|---|
| zone_id | text PK | |
| name | text | |
| type | text | PROHIBITED / RESTRICTED / DANGER / CTR / TMA / ADIZ / CAP / AAR_TRACK / CORRIDOR / TRA |
| geometry | geometry(Polygon,4326) | or LineString for corridors with `width_km` |
| floor_ft, ceiling_ft | int | AMSL |
| active_from, active_to | timestamptz | null = permanent |
| controlling_authority | text | civil ATC / IAF |
| source | text | openaip / synthetic |
| capacity_per_hour | int | max simultaneous flights (for deconfliction) |

## Generation rules
- Import OpenAIP India airspaces, clip to theatre bounding box.
- Add 6-10 CAP boxes (80x40 km) near borders, 3 tanker racetracks (200 km legs), 4 corridors (width 20 km), time-windowed TRAs activated by missions.
- Border/ADIZ polygon from Natural Earth buffered.

## Storage
```sql
CREATE TABLE airspace_zone (zone_id text PRIMARY KEY, name text, type text,
  geometry geometry(Geometry,4326), floor_ft int, ceiling_ft int,
  active_from timestamptz, active_to timestamptz, capacity_per_hour int, source text);
CREATE INDEX ON airspace_zone USING gist (geometry);
```
Active-zone cache in Redis: `airspace:active` (set of zone_ids) refreshed each minute. Neo4j `(:AirspaceZone)` nodes only for zones linked to missions. Raw OpenAIP files in MinIO `raw/openaip/`. Kafka `garuda.airspace.activation`.

## Key queries
```sql
-- zones intersecting a route at altitude and time
SELECT zone_id FROM airspace_zone
WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromGeoJSON(:route),4326))
  AND :alt_ft BETWEEN floor_ft AND ceiling_ft
  AND (active_from IS NULL OR :t BETWEEN active_from AND active_to);
```

## Consumers
Route planning (blocked cells), airspace deconfliction, harness `check_airspace`.
