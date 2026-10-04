# Aircraft Availability

## Purpose
Current and forecast readiness of every airframe: can it fly, where is it, with what fuel, until when.

## Source
Synthetic (fictional fleet). Types: Su-30MKI, Rafale, Tejas, Mirage-2000, Jaguar, C-130J, IL-78 (tanker), AEW&C, helicopter.

## Schema
| Field | Type | Unit | Constraints |
|---|---|---|---|
| tail_no | text | | PK, e.g. `SU-A0123` |
| type | text | | enum of fleet types |
| sqn_id | text | | FK squadron |
| base_id | text | | FK base (current location) |
| status | text | | FMC / PMC / NMC / AOG / IN_MISSION |
| status_reason | text | | e.g. `radar_fault`, `scheduled_inspection` |
| fuel_kg | real | kg | 0..capacity |
| flight_hours_total | real | h | >= 0 |
| hours_to_next_inspection | real | h | >= 0 |
| maint_return_eta | timestamptz | | null if FMC |
| turnaround_min | int | min | per-type mean |
| capabilities | text[] | | `BVR`, `AG_PGM`, `ISR`, `AAR_RECEIVER` ... |
| range_km, cruise_kts, ceiling_ft | real | | per type |
| updated_at | timestamptz | | |

## Synthetic generation rules
- Fleet counts per `scenario_definition.md`.
- Status mix: FMC 70-75%, PMC 10-12%, NMC 13-18% (Jaguar/Mirage older: lower FMC).
- `flight_hours_total` ~ Gamma(shape=4, scale=1500 h); `hours_to_next_inspection` ~ Uniform(0, 100/200 h cycle).
- NMC repair time ~ LogNormal(mu=ln(18h), sigma=0.7).
- Events: random failure with hazard proportional to hours since inspection (feeds survival model); post-mission status degradation probability 3-5% per sortie.

## Storage
**Postgres (system of record)**
```sql
CREATE TABLE aircraft (
  tail_no text PRIMARY KEY, type text NOT NULL, sqn_id text, base_id text,
  status text NOT NULL, status_reason text, fuel_kg real,
  flight_hours_total real, hours_to_next_inspection real,
  maint_return_eta timestamptz, capabilities text[], updated_at timestamptz DEFAULT now()
);
CREATE INDEX ON aircraft (base_id, status);
```
**TimescaleDB** hypertable `aircraft_status_history(time, tail_no, status, base_id)` for forecasting training data, 90-day retention.
**Neo4j** `(:Aircraft)-[:ASSIGNED_TO]->(:Squadron)`.
**Redis** hash `ac:{tail_no}` (live status, fuel, base) + set `base:{base_id}:fmc`.
**Kafka** topic `garuda.aircraft.status` key=tail_no.

## Update frequency
Event-driven; simulator emits status changes, fuel updates every 15 sim-min.

## Sample
```json
{"tail_no":"SU-A0123","type":"Su-30MKI","base_id":"B1","status":"FMC","fuel_kg":5200,"hours_to_next_inspection":37.5,"capabilities":["BVR","AG_PGM","AAR_RECEIVER"]}
```

## Pydantic
```python
class AircraftState(BaseModel):
    tail_no: str
    type: str
    base_id: str
    status: Literal["FMC","PMC","NMC","AOG","IN_MISSION"]
    fuel_kg: float
    hours_to_next_inspection: float
    capabilities: list[str]
```

## Consumers
`get_aircraft_availability`, MILP assignment, availability forecast, sortie generation, retasking.
