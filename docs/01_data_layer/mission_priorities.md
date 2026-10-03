# Mission Priorities

## Purpose
The demand side: requested missions (ATO-style) with priority, timing, targets and required capabilities.

## Source
Synthetic.

## Schema
`mission`:
| Field | Type | Notes |
|---|---|---|
| mission_id | text PK | |
| type | text | DCA, CAP, STRIKE, SEAD, CAS, ISR, AAR, AIRLIFT, SAR |
| priority | int | 1 (highest) - 5 |
| value | real | commander value points (default from priority: 100,60,35,20,10) |
| requested_by | text | |
| window_start, window_end | timestamptz | time-on-target window |
| duration_min | int | |
| target_id | text | FK target (null for CAP/AAR) |
| area | geometry | for CAP/ISR |
| required_capabilities | text[] | e.g. `BVR`, `AG_PGM` |
| min_aircraft, max_aircraft | int | package size |
| required_effect | text | DESTROY / SUPPRESS / OBSERVE / DENY |
| min_pk | real | required confidence |
| status | text | REQUESTED / PLANNED / APPROVED / EXECUTING / DONE / CANCELLED |
| dependencies | text[] | e.g. SEAD before STRIKE |

`target`: `target_id, class (RUNWAY, RADAR, BRIDGE, ARMOUR, C2, FUEL_DEPOT), position, value, hardness, defended_by[]`.

## Generation rules
- 40-60 missions across the 72 h horizon; priority distribution P1 10%, P2 20%, P3 35%, P4 25%, P5 10%.
- Types weighted: DCA/CAP 25%, STRIKE 20%, CAS 15%, ISR 15%, SEAD 10%, AAR 10%, AIRLIFT 5%.
- Time windows 2-6 h; dependent packages (SEAD -> STRIKE offset of 30 min).
- Dynamic arrival: Poisson process, rate rising during scenario escalation; emergency P1 CAS injected at T+30h.

## Storage
Postgres tables `mission`, `target`; Neo4j `(:Mission)-[:TARGETS]->(:Target)`, `(:Plan)-[:INCLUDES]->(:Mission)`; Redis sorted set `missions:pending` score=priority; Kafka `garuda.mission.request`; `plan` and `plan_assignment` tables hold solver outputs with versions (audit).

## Consumers
MILP assignment, retasking, multi-objective tradeoffs, Monte Carlo evaluation.
