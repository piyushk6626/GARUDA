# Threats

## Purpose
Hostile air-defence and air assets that constrain routes and affect mission success.

## Source
Fully synthetic and generic ("Red" force). No real order of battle.

## Schema (`threat`)
| Field | Type | Notes |
|---|---|---|
| threat_id | text PK | |
| type | text | SAM_LONG / SAM_MED / SAM_SHORT / RADAR_EW / RADAR_GCI / AAA / ENEMY_CAP / AIRFIELD |
| position | geometry(Point,4326) | static; mobile threats have track history |
| mobile | bool | |
| engagement_range_km | real | lethal radius |
| detection_range_km | real | |
| min_alt_ft, max_alt_ft | int | engagement envelope |
| pk_base | real | single-shot kill prob at close range |
| confidence | real 0-1 | intel confidence |
| first_seen, last_seen | timestamptz | |
| status | text | ACTIVE / SUPPRESSED / DESTROYED / UNKNOWN |

`threat_track(time, threat_id, geom, speed_kts, heading)` for mobile units / ENEMY_CAP.

## Generation rules
- 12-20 fixed sites near border at plausible clusters; 2-4 mobile SAM units moving in random walk along roads (OSM roads).
- Range by type: long 150-250 km, medium 40-80, short 5-20 (fictional).
- Confidence decays: `conf(t) = conf0 * exp(-(t - last_seen)/tau)`, tau = 12 h for mobile, 72 h for fixed; reset on new sighting.
- Events: new pop-up SAM, site suppressed after SEAD mission, position update with error ellipse.

## Derived: threat exposure surface
Rasterise to grid: `risk(cell, alt) = 1 - prod_i (1 - pk_i * conf_i * I(dist <= range_i, alt in envelope))`. Stored as a `.npy`/GeoTIFF in MinIO and cached in Redis per version; recomputed on threat events.

## Storage
```sql
CREATE TABLE threat (threat_id text PRIMARY KEY, type text, position geometry(Point,4326),
  mobile bool, engagement_range_km real, detection_range_km real, min_alt_ft int, max_alt_ft int,
  pk_base real, confidence real, first_seen timestamptz, last_seen timestamptz, status text);
CREATE INDEX ON threat USING gist (position);
```
Redis `threat:active` geo set (`GEOADD`) for radius queries. Neo4j `(:Threat)-[:COVERS]->(:Target)`. Kafka `garuda.threat.update`.

## Consumers
Route planning, WTA, mission success model, anomaly detection, harness `get_threats_in_area`.
