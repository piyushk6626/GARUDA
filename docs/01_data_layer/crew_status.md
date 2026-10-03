# Crew Status

## Purpose
Who can fly what, when, and how fatigued they are.

## Source
Synthetic (no real personnel data; use Faker with Indian-name locale `en_IN`, or pseudonymous callsigns).

## Schema
| Field | Type | Unit | Constraints |
|---|---|---|---|
| crew_id | text | | PK |
| callsign | text | | |
| role | text | | PILOT / WSO / LOADMASTER / AEW_OP |
| sqn_id, base_id | text | | FK |
| qualifications | jsonb | | `{type: [roles]}`, e.g. `{"Su-30MKI":["BVR","AG"]}` |
| currency | jsonb | | last-flown dates per qualification, night, AAR |
| flight_hours_total / last_30d | real | h | |
| duty_hours_24h | real | h | max 14 |
| rest_hours_since_last_duty | real | h | min 12 |
| fatigue_score | real | 0-1 | from fatigue model |
| medical_status | text | | FIT / LIMITED / GROUNDED |
| leave_until | timestamptz | | nullable |
| current_assignment | text | | mission_id |
| updated_at | timestamptz | | |

## Generation rules
- ~2 crew per fighter airframe (pilot, plus WSO for two-seaters); ~300 total.
- Experience: total hours ~ Gamma(3, 900); flight leads = top 25%.
- Qualifications: each crew 1 primary type, 20% a secondary; AAR currency 60%; night 70%.
- Medical: 93% FIT, 5% LIMITED, 2% GROUNDED. Leave: 5% on leave.
- Duty/rest evolves via simulator; fatigue updated hourly.

## Storage
```sql
CREATE TABLE crew (
  crew_id text PRIMARY KEY, callsign text, role text, sqn_id text, base_id text,
  qualifications jsonb, currency jsonb, flight_hours_30d real,
  duty_hours_24h real, rest_hours real, fatigue_score real,
  medical_status text, leave_until timestamptz, current_assignment text
);
CREATE INDEX ON crew USING gin (qualifications);
```
TimescaleDB `crew_duty_log(time, crew_id, duty_hours_24h, fatigue_score)`. Neo4j `(:Crew)-[:QUALIFIED_ON]->(:Aircraft type)` and `[:MEMBER_OF]`. Redis `crew:{id}`. Kafka `garuda.crew.status`.

## Privacy
Pseudonymise; never ingest real personal data. Role-based access in the API.

## Consumers
Crew scheduling CP-SAT, fatigue model, MILP assignment (crew availability).
