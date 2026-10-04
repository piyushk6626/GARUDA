# Demo Plan: Operation Sindoor Reconstruction

A recorded demo of GARUDA running a decision-support reconstruction of Operation Sindoor (6–10 May 2025), using the public narrative in `docs/SINDORE.MD`.

**Data policy for the demo (say this on screen in the first 10 seconds):**
- Geography, place names, the timeline, and the sequence of phases follow public reporting.
- Locations are approximate **town-level centroids**, not facility coordinates.
- Force strengths, tail numbers, crew, weapon and sensor performance, Pk values, stock levels, drone counts per site, and BDA results are **synthetic and fictional**, consistent with the repo policy ("all military data synthetic and unclassified").
- Real weapon names appear only as **display aliases** ("SCALP-class"). Their data rows use generic codes with fictional parameters.

---

## 1. What the demo must prove

The closing lesson of `SINDORE.MD` (section 17) is the demo's thesis. Each act maps to one sentence of it:

| SINDORE.MD requirement | Where the demo proves it |
|---|---|
| "collect the latest information" | Act 1 situation brief, Act 3 threat picture, Act 5 swarm picture |
| "identify conflicts" | Act 2 validator panel (ROE, border, civil traffic, crew rest), Act 7 aircraft-loss conflict |
| "calculate possible mission options" | Act 2 strike plan, Act 4 SEAD route alternatives, Act 7 Pareto options |
| "show the risks of each option" | Act 2 Monte Carlo, Act 4 risk heat map before/after, Act 7 option comparison |
| "help the commander select a course of action" | Approval cards in every act |
| "the final decision remains with the commander" | Nothing is committed without the Approve click. The agent never calls `commit_plan`. |

The core problem statement in `README.md` is **dynamic retasking**. Acts 3, 5, 7, and 8 are retasking moments, and they carry the demo.

---

## 2. Demo structure at a glance

Scenario clock: T0 = `2025-05-06T12:00:00Z` (17:30 IST, 6 May). Horizon: 98 hours. All data is stored in UTC; the UI shows IST.

| Act | Scenario time (IST) | T+ (h) | ROE phase | Story beat | GARUDA capability shown | Approx. video length |
|---|---|---|---|---|---|---|
| 0 | 6 May 17:30 | 0 | — | Cold open: theatre map | Data fusion, map layers | 0:40 |
| 1 | 6 May 17:30 | 0 | P1 | Intel brief: nine targets | Agent mode, retrieval, spatial | 1:00 |
| 2 | 6 May 18:00 | 0.5 | P1 | Stand-off strike plan, TOT 01:05–01:30 | Plan mode: WTA, MILP, routes, AAR, deconfliction, Monte Carlo, approval | 2:30 |
| 2b | 7 May 01:05–02:30 | 7.6–9 | P1 | Execution and BDA | Clock playback, BDA ingest, ISR re-look | 0:40 |
| 3 | 8 May 00:00 | 30.5 | P1 | Drones and missiles toward 8 stations | Event injection, threat query, DCA retask | 1:15 |
| 4 | 8 May 06:00 | 36.5 | P2 | Long-range SAM near Lahore, SEAD | ROE change, route risk with and without SEAD | 1:15 |
| 5 | 8 May 20:00 – 9 May 01:30 | 50.5–56 | P2 | 300–400 drones at 36 locations | Swarm picture, anomaly detection, counter-UAS allocation | 1:45 |
| 6 | 9 May 02:30 | 57 | P2 | Armed drones on 4 AD sites, 1 radar | Loitering munition WTA | 0:45 |
| 7 | 9 May 21:00 – 10 May 05:00 | 75.5–83.5 | P3 | Airbase strikes, mid-plan aircraft loss | Pareto options, rolling-horizon retask, diff | 2:00 |
| 8 | 10 May 17:00 | 95.5 | P4 | Cessation | Retask to defensive posture, posture summary | 0:45 |
| — | — | — | — | Close: KPI recap | | 0:30 |

Full cut: about 13 minutes. A 3-minute highlight cut uses Acts 0, 2 (plan reveal and approval only), 5, and 7 (retask diff only).

---

## 3. Screen layout the app needs

The repo has no frontend yet (the Makefile `web` target points to `frontend/`, which does not exist). The demo needs this one-page layout.

```
+--------------------------------------------------------------------------------+
| GARUDA | Clock 07 May 01:12 IST [>] [>>] [jump to act v] | ROE: P1 TERROR INFRA |
+-------------------+--------------------------------------------+---------------+
| CHAT / AGENT      |                                            | PLAN PANEL    |
| - commander msgs  |                  MAP                       | - work plan   |
| - step progress   |                                            | - assignments |
|   (s1 retrieval…) |                                            | - KPIs        |
| - answers with    |                                            | - validator   |
|   cited step ids  |                                            | - [Approve]   |
|                   |                                            |   [Reject]    |
+-------------------+--------------------------------------------+---------------+
| TIMELINE: phase bands P1|P2|P3|P4, event markers, mission bars (TOT), now-line |
+--------------------------------------------------------------------------------+
```

- **Top bar**: scenario clock in IST, play/fast-forward, "jump to act" dropdown, ROE phase badge (color changes per phase), and a threat-level chip.
- **Chat panel**: renders SSE events from `/api/chat`: `session`, step start and finish, sub-agent questions, the work-plan approval card, the final answer.
- **Plan panel**: work plan steps with status icons, assignment table, KPIs, validator conflicts, and the approval buttons that call `/api/chat/{session_id}/resume`.
- **Timeline**: phase bands, event markers (click to inspect), Gantt bars for missions (window and TOT), and a now-line synced to the clock.

### 3.1 Map layers (fixed visual language)

| Layer | Geometry | Style | Source |
|---|---|---|---|
| IB / LoC | `india_border.geojson` | IB solid white, LoC dashed white | existing fixture |
| No-cross zone | Pakistan-side polygon from the border, 10 km buffer | Faint red hatch, label "BLUE NO-CROSS (ROE)" | new zone, type `PROHIBITED` |
| Blue bases | points | Blue airfield icon; status ring green (open), amber (degraded), red (closed) | `bases` |
| Defended sites (36) | points | Small blue shield; badge with drone count during Act 5 | new `defended_site` |
| Friendly AD | circles | Blue rings per layer: guns 3–4 km, C-UAS 8 km, short SAM 10 km, medium SAM 25 km | new `friendly_ad` |
| Targets | points | Numbered red diamonds 1–9 (P1), orange squares for AD/radar (P2), purple runway icons for airbases (P3) | `targets` |
| Border distance | line target to nearest border point | Thin dashed grey with km label | computed via `spatial_query` |
| Hostile threats | circles | Engagement ring solid red, detection ring dotted red; grey when SUPPRESSED, black X when DESTROYED | `threats` |
| Launch baskets | polygons | Green translucent, Indian side only, label `LB-T03 (PGM-MOD)` | new computation |
| Blue routes | polylines | Solid blue per package, arrowheads, waypoint dots with ETA tooltips | `plan_route` output |
| Weapon flyout | line launch point to target | Dashed yellow, animated dash during execution | derived from assignment |
| Tanker tracks | polylines | Cyan racetrack with tanker callsign | `schedule_tanker_aar` |
| CAP orbits | polygons | Blue racetrack outlines with callsign and on-station times | DCA missions and airspace `CAP` |
| Hostile air tracks | polylines and moving points | Red; drones as small triangles, cruise missiles as larger red arrows | new `air_track` |
| Engagements | points | Red X for kinetic kill, purple bolt for soft kill, orange "!" for leaker | sim output |
| Route risk heat map | raster grid | Transparent to red, toggled | `plan_route` risk field |
| Civil traffic | points | Grey plane icons | ADS-B |
| NOTAM closures | polygons and airports | Grey hatched; closed airports with grey X | new airspace zones |
| Weather | grid | Existing flyability layer | `weather` |

Act transitions are clearer on video if the map auto-fits to the act's area of interest. Add a `fit_bounds` hint to each act.

---

## 4. Data points required

Each row says what is needed, the value or rule for the demo, and whether the repo already has it.

Status key: **Exists**: works today. **Extend**: schema or generator change. **New**: new model, generator, or tool.

### 4.1 Scenario and time

| Data point | Demo value | Status |
|---|---|---|
| `start` | `2025-05-06T12:00:00Z` | Extend (config value) |
| `horizon_hours` | 98 | Exists |
| Bounding box | lat 22.5–35.5, lon 66.5–78.5 (covers Muzaffarabad 34.4°N, Leh 77.6°E, Bholari 68.0°E) | Extend (current 22–33 / 68–78 excludes Muzaffarabad and Bholari) |
| ROE phases | P1 terror infrastructure only, P2 adds hostile AD and radar, P3 adds airbases, C2, storage, P4 defensive only | New |
| Display timezone | IST | New (UI) |

### 4.2 ROE profile (new, drives every solver)

| Phase | Active from (T+) | Allowed target classes | Cross IB/LoC | Notes |
|---|---|---|---|---|
| P1 | 0 | `FACILITY` | No (weapons only) | "No Pakistani military installation" is a hard filter |
| P2 | 36.0 | + `AD_SITE`, `RADAR` | No | Triggered by the threat to Indian installations |
| P3 | 75.5 | + `RUNWAY`, `AIRBASE`, `C2`, `AMMO_DEPOT` | No | Military infrastructure |
| P4 | 95.5 | none (offensive tasking cancelled) | No | Ceasefire at 17:00 IST, 10 May |

Stored as `config/scenarios/sindoor/roe.yaml`. Enforced in three places: mission filtering in `assign_missions`, target filtering in `assign_weapons_to_targets`, and a new rule in `validation/rules.py` so the validator flags any violation.

### 4.3 Blue bases

The existing seven stay. Add the stations SINDORE.MD names, plus Leh and Hindan.

| base_id | Name | ICAO | Lat | Lon | Elev ft | Role in demo | Status |
|---|---|---|---|---|---|---|---|
| B1 | Jodhpur | VIJO | 26.2511 | 73.0489 | 717 | Su-30 strike (south) | Exists |
| B2 | Jaisalmer | VIJR | 26.8887 | 70.8650 | 751 | Forward, Jaguar | Exists |
| B3 | Bikaner (Nal) | VIBK | 28.0706 | 73.2072 | 752 | Tejas DCA | Exists |
| B4 | Ambala | VIAM | 30.3681 | 76.8167 | 900 | Rafale stand-off strike | Exists |
| B5 | Adampur | VIAX | 31.4338 | 75.7588 | 775 | Mirage, defended station | Exists |
| B6 | Bhuj | VABJ | 23.2878 | 69.6702 | 268 | Defended station, helicopters | Exists |
| B7 | Agra | VIAG | 27.1578 | 77.9609 | 551 | IL-78 tankers, AEW&C | Exists |
| B8 | Srinagar | VISR | 33.9871 | 74.7742 | 5429 | Defended station, DCA | New |
| B9 | Jammu | VIJU | 32.6891 | 74.8374 | 1029 | Defended station | New |
| B10 | Pathankot | VIPK | 32.2336 | 75.6344 | 1017 | Defended station | New |
| B11 | Amritsar | VIAR | 31.7096 | 74.7973 | 756 | Defended station | New |
| B12 | Bathinda | VIBT | 30.2701 | 74.7558 | 662 | Defended station, Su-30 | New |
| B13 | Chandigarh | VICG | 30.6735 | 76.7885 | 1012 | Defended station, transport | New |
| B14 | Leh | VILH | 34.1359 | 77.5465 | 10682 | Northern anchor of drone wave | New |

Required changes: add rows to `backend/fixtures/gis/ourairports_airports.csv` and `ourairports_runways.csv` (none of these ICAOs are there today), extend `BASES` in `bases.py` (or load bases from the scenario pack), and set `bases: 14`. Coordinates are public OurAirports values, rounded; re-pull from OurAirports when adding the rows.

### 4.4 Blue fleet (fictional numbers)

Keep `fleet` from `config/scenario.yaml`. Rehome for the story:

| Type | Count | Home base(s) | Demo role |
|---|---|---|---|
| Rafale | 24 | B4 Ambala | Act 2 stand-off strike (SCALP-class, HAMMER-class) |
| Su-30MKI | 40 | B1 Jodhpur 16, B12 Bathinda 12, B8 Srinagar 12 | Act 7 supersonic cruise missile, DCA |
| Mirage-2000 | 18 | B5 Adampur | Act 2 alternates, Act 3 DCA |
| Tejas | 24 | B3 Bikaner | DCA |
| Jaguar | 12 | B2 Jaisalmer | Reserve strike |
| IL-78 | 6 | B7 Agra | AAR for Rafale packages |
| AEW&C | 4 | B7 Agra | Orbit over Punjab (display only) |
| C-130J / Heli | 8 / 14 | B6 Bhuj, B13 Chandigarh | Background |

Forced states for the story (override after random generation):
- At T0, ≥ 14 Rafale FMC at B4 so Act 2 is feasible.
- At T+79, two Su-30MKI at B1 go NMC (`aircraft_nmc` event). This is the Act 7 retask trigger.
- Crew: Rafale crews at B4 rested at T0; flight-duty logs make some Act 2 crews ineligible again by Act 7, so `schedule_crew` shows fatigue reason codes.

Change needed: `HOME_BASE` in `generators/aircraft.py` maps one type to one base. Extend it to accept a split (`{"Su-30MKI": {"B1": 16, "B12": 12, "B8": 12}}`) from the scenario pack.

### 4.5 Weapons (fictional parameters, generic codes)

Existing catalog in `generators/weapons.py` stays. Add:

| Code | Display alias | Category | Range km | Weight kg | Guidance | Pk band | Carriers / stations | Status |
|---|---|---|---|---|---|---|---|---|
| `CM-STANDOFF-L` | SCALP-class | `ASM` | 250 | 1300 | GPS/INS + IIR | 0.85–0.95 | Rafale S3 (1), Mirage S3 (1) | New |
| `PGM-MOD` | HAMMER-class | `ASM` | 60 | 340 | GPS/INS + IIR | 0.80–0.92 | Rafale S2, S4 (up to 3 each) | New |
| `CM-SUPERSONIC` | BrahMos-A-class | `ASM` | 300 | 2500 | INS + active seeker | 0.85–0.95 | Su-30MKI S5 (1) | New |
| `LM-ARM` | Loitering munition | `ANTI_RADIATION` | 100 | 120 | Passive RF + EO | 0.70–0.85 | Ground launcher `GL-1` | New |

Required changes:
- Su-30 station S5 limit is 2000 kg; raise to 2500 for `CM-SUPERSONIC`. Rafale S3 limit is 2000 kg (enough).
- Add `_Recipe("STRIKE_SO", …)` stand-off recipes for Rafale (`CM-STANDOFF-L` + 2× AAM-WVR + 2× AAM-BVR-MED), Rafale (`PGM-MOD` ×6), and Su-30 (`CM-SUPERSONIC` + self-defence).
- Scarce stock: `CM-STANDOFF-L` 18 at B4, `PGM-MOD` 40 at B4, `CM-SUPERSONIC` 16 split B1/B12, `LM-ARM` 12 at `GL-1`. Scarcity is what makes the optimizer interesting.
- Ground launcher: add a pseudo-base `GL-1` near Pathankot (32.25, 75.40) with no runway, used only by the weapon-target assignment in Act 6.
- Display alias: new optional `display_name` field on `WeaponType`.

### 4.6 Targets

`Target` needs new fields: `name`, `phase` (P1, P2, P3), `group` (`TERROR_INFRA`, `AD`, `AIRBASE`), `collateral_sensitivity` (0–1, for the collateral constraint), and `aim_points` (int). `TargetClass` needs `FACILITY`, `AD_SITE`, `AIRBASE`, `AMMO_DEPOT`.

**Phase 1: nine designated sites (approximate town-level positions)**

| target_id | Name | Near | Lat | Lon | Class | Value | Hardness | Collateral sens. | Expected weapon (from border distance) |
|---|---|---|---|---|---|---|---|---|---|
| T-S01 | Sawai Nala | Muzaffarabad | 34.38 | 73.47 | FACILITY | 80 | 0.45 | 0.6 | PGM-MOD or CM-STANDOFF-L |
| T-S02 | Syedna Bilal | Muzaffarabad | 34.36 | 73.48 | FACILITY | 75 | 0.45 | 0.7 | PGM-MOD or CM-STANDOFF-L |
| T-S03 | Gulpur | Kotli | 33.47 | 73.95 | FACILITY | 70 | 0.40 | 0.4 | PGM-MOD |
| T-S04 | Barnala | Bhimber | 33.05 | 74.05 | FACILITY | 60 | 0.40 | 0.5 | PGM-MOD |
| T-S05 | Markaz Abbas | Kotli | 33.52 | 73.90 | FACILITY | 75 | 0.50 | 0.6 | PGM-MOD |
| T-S06 | Sarjal | Sialkot sector | 32.35 | 75.02 | FACILITY | 65 | 0.40 | 0.5 | PGM-MOD |
| T-S07 | Mehmoona Joya | Sialkot | 32.53 | 74.60 | FACILITY | 65 | 0.40 | 0.5 | PGM-MOD |
| T-S08 | Markaz Taiba | Muridke | 31.80 | 74.26 | FACILITY | 100 | 0.55 | 0.8 | CM-STANDOFF-L |
| T-S09 | Markaz Subhan Allah | Bahawalpur | 29.39 | 71.68 | FACILITY | 100 | 0.55 | 0.8 | CM-STANDOFF-L (only option at ~100 km from IB) |

The "expected weapon" column is not data. It is the result the demo hopes the solver reaches, and it is a good acceptance check (see section 9). Refine positions from OSM place nodes; ±10 km is fine for a synthetic demo.

**Phase 2: hostile air defence and radar (fictional sites near public towns)**

| target_id / threat_id | Name | Lat | Lon | Threat type | Engagement km | Detection km | Notes |
|---|---|---|---|---|---|---|---|
| TH-LHR-01 | Lahore AD (long-range SAM) | 31.52 | 74.40 | SAM_LONG | 180 | 260 | Appears at T+36.5. Ring covers Amritsar and parts of the Act 2 northern routes. Act 4 suppresses it. |
| TH-LHR-02 | Lahore EW radar | 31.45 | 74.20 | RADAR_EW | 0 | 350 | Cues TH-LHR-01 |
| TH-AD-01 | Sialkot sector SAM | 32.45 | 74.45 | SAM_MED | 60 | 90 | Act 6 target |
| TH-AD-02 | Pasrur sector SAM | 32.25 | 74.65 | SAM_MED | 60 | 90 | Act 6 target |
| TH-AD-03 | Okara sector SAM | 30.80 | 73.45 | SAM_MED | 60 | 90 | Act 6 target |
| TH-AD-04 | Bahawalnagar sector GCI radar | 29.99 | 73.25 | RADAR_GCI | 0 | 250 | Act 6 priority radar (the one destroyed) |
| TH-AAA-xx | Point AAA at each P1 site | per site | per site | AAA | 8 | 12 | Two per P1 target, shows why stand-off matters |

**Phase 3: airbases (approximate public positions)**

| target_id | Name | Lat | Lon | Class | Aim points | Value | Notes |
|---|---|---|---|---|---|---|---|
| T-A01 | Noor Khan | 33.62 | 73.10 | AIRBASE | runway, C2 | 100 | Deep, ~90 km from LoC |
| T-A02 | Rafiqui | 30.76 | 72.28 | AIRBASE | runway, shelters | 90 | |
| T-A03 | Murid | 32.91 | 72.77 | AIRBASE | runway, UAV hangar | 85 | |
| T-A04 | Sargodha | 32.05 | 72.67 | AIRBASE | runway, C2 | 100 | |
| T-A05 | Rahim Yar Khan | 28.38 | 70.28 | AIRBASE | runway | 70 | |
| T-A06 | Jacobabad | 28.28 | 68.45 | AIRBASE | runway, ammo | 85 | |
| T-A07 | Sukkur | 27.72 | 68.79 | AIRBASE | runway, radar | 75 | |
| T-A08 | Bholari | 25.24 | 67.97 | AIRBASE | hangar | 80 | Farthest; CM-SUPERSONIC only |

Each airbase also gets an `AIRFIELD` threat (point defence) and one `SAM_MED` within 15 km, generated from the template rule in section 5.4.

### 4.7 Friendly air defence and counter-UAS (new domain)

New schema `FriendlyAdUnit`: `unit_id`, `site_id`, `system_class`, `lat`, `lon`, `min_range_km`, `max_range_km`, `max_alt_ft`, `kill_mode` (`KINETIC` or `SOFT`), `channels` (simultaneous engagements), `rounds` (missiles or bursts), `reload_min`, `pk_vs` (per hostile class), `cost_per_shot` (relative units), `status`.

| system_class | Display alias | Range km | Kill mode | Pk vs UAS | Pk vs cruise missile | Relative cost per shot | Placement |
|---|---|---|---|---|---|---|---|
| `GUN-LL` | L70 / ZU-23-class guns | 0.3–4 | KINETIC | 0.45 | 0.10 | 1 | 2–4 per defended site |
| `CUAS-SOFT` | Integrated C-UAS (jam/spoof) | 0–8 | SOFT | 0.55 | 0.00 | 0.1 | 1 per defended site |
| `CUAS-HARD` | C-UAS hard kill | 0.5–6 | KINETIC | 0.65 | 0.05 | 3 | Major stations only |
| `SAM-SR` | OSA-AK-class | 1.5–10 | KINETIC | 0.70 | 0.50 | 40 | Bases B5, B8–B12 |
| `SAM-MR-P` | Pechora-class | 3.5–25 | KINETIC | 0.70 | 0.55 | 60 | B9, B10, B11 |
| `SAM-MR-A` | Akash-class | 4–25 | KINETIC | 0.80 | 0.70 | 100 | B5, B8, B9, B10, B12, B13, B6 |

All numbers fictional. `cost_per_shot` drives the doctrine point in Act 5: do not spend medium SAMs on cheap drones.

Stock is the constraint: `rounds` per unit is small (Akash-class 8–16, Pechora-class 4–8), so the swarm visibly drains interceptors and the Act 5 forecast question has a real answer.

### 4.8 Defended sites for the drone wave (36)

New schema `DefendedSite`: `site_id`, `name`, `lat`, `lon`, `asset_value` (1–100), `base_id` (nullable), `sector` (`NORTH`, `CENTRAL`, `SOUTH`).

| # | Site | Lat | Lon | Sector | # | Site | Lat | Lon | Sector |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Leh | 34.16 | 77.58 | NORTH | 19 | Amritsar | 31.63 | 74.87 | CENTRAL |
| 2 | Kargil | 34.55 | 76.13 | NORTH | 20 | Tarn Taran | 31.45 | 74.93 | CENTRAL |
| 3 | Baramulla | 34.20 | 74.34 | NORTH | 21 | Ferozepur | 30.93 | 74.61 | CENTRAL |
| 4 | Kupwara | 34.53 | 74.26 | NORTH | 22 | Fazilka | 30.40 | 74.03 | CENTRAL |
| 5 | Uri | 34.08 | 74.03 | NORTH | 23 | Jalandhar | 31.33 | 75.58 | CENTRAL |
| 6 | Srinagar | 34.08 | 74.80 | NORTH | 24 | Adampur | 31.43 | 75.76 | CENTRAL |
| 7 | Awantipora | 33.92 | 75.01 | NORTH | 25 | Bathinda | 30.21 | 74.95 | CENTRAL |
| 8 | Poonch | 33.77 | 74.09 | NORTH | 26 | Chandigarh | 30.73 | 76.78 | CENTRAL |
| 9 | Rajouri | 33.38 | 74.31 | NORTH | 27 | Sri Ganganagar | 29.90 | 73.88 | SOUTH |
| 10 | Nowshera | 33.15 | 74.23 | NORTH | 28 | Suratgarh | 29.32 | 73.90 | SOUTH |
| 11 | Akhnoor | 32.89 | 74.74 | NORTH | 29 | Bikaner | 28.02 | 73.31 | SOUTH |
| 12 | Jammu | 32.73 | 74.86 | NORTH | 30 | Phalodi | 27.13 | 72.36 | SOUTH |
| 13 | Nagrota | 32.79 | 74.90 | NORTH | 31 | Jaisalmer | 26.92 | 70.91 | SOUTH |
| 14 | Samba | 32.56 | 75.12 | NORTH | 32 | Barmer | 25.75 | 71.39 | SOUTH |
| 15 | Kathua | 32.37 | 75.52 | NORTH | 33 | Uttarlai | 25.81 | 71.48 | SOUTH |
| 16 | Udhampur | 32.92 | 75.14 | NORTH | 34 | Bhuj | 23.25 | 69.67 | SOUTH |
| 17 | Pathankot | 32.27 | 75.65 | NORTH | 35 | Naliya | 23.26 | 68.84 | SOUTH |
| 18 | Gurdaspur | 32.04 | 75.40 | NORTH | 36 | Sir Creek | 23.75 | 68.30 | SOUTH |

`asset_value`: 90–100 for sites with a base (Srinagar, Jammu, Pathankot, Amritsar, Adampur, Bathinda, Chandigarh, Bhuj, Leh, Bikaner, Jaisalmer, Uttarlai, Naliya), 50–70 for cantonments, 20–40 for towns.

### 4.9 Hostile air tracks (new: drones and missiles)

New schema `AirTrack` (separate from `ThreatTrack`, which is hourly ground movement): `track_id`, `raid_id`, `kind` (`UAS_ARMED`, `UAS_DECOY`, `LOITERING`, `CRUISE_MISSILE`), `origin_lat/lon`, `aim_site_id`, `launch_time`, `samples[]` at 30 s (`time`, `lat`, `lon`, `alt_ft`, `speed_kts`), `fate` (`KILLED_KINETIC`, `KILLED_SOFT`, `LEAKED`, `LOST`), `fate_time`, `engaged_by`.

| Raid | T+ | Sites | Total tracks | Mix |
|---|---|---|---|---|
| R1 (Act 3) | 30.5–32.0 | Jammu, Srinagar, Pathankot, Amritsar, Adampur, Bathinda, Chandigarh, Bhuj | 60 | 45 UAS_ARMED, 10 UAS_DECOY, 5 CRUISE_MISSILE |
| R2 (Act 5) | 50.5–56.0 | All 36 sites | 350 | 60% UAS_ARMED (Songar-class alias), 25% UAS_DECOY, 15% LOITERING |
| R3 (Act 7) | 75.5–77.0 | Northern and central bases | 40 | 30 UAS_ARMED, 10 CRUISE_MISSILE |

Fates are not hand-written. They come from the engagement simulation in section 5.5, so the counts in the chat answer match the map.

### 4.10 Airspace and civil traffic

| Data point | Demo value | Status |
|---|---|---|
| Blue no-cross zone | Pakistan-side polygon from `india_border.geojson` buffered 10 km, type `PROHIBITED`, `controlling_authority: "ROE"` | New zone (existing type) |
| Civil airport closures | NOTAM zones (5 NM radius) over northern civil airports B8–B14 plus Dharamshala, Kullu, Shimla, Bhuntar, from T+6 to end | New zones, type `RESTRICTED` |
| Pakistan airspace closure | Red-side FIR polygon closed to Blue civil traffic | New zone |
| Civil reroutes | 25 synthetic ADS-B tracks Delhi–Gulf rerouted south over Gujarat/Arabian Sea; 10 Delhi–Europe tracks rerouted | Extend `generators/adsb.py` |
| AAR tracks | 2 racetracks: "ANCHOR NORTH" over Punjab south of Ludhiana (30.6, 75.9) and "ANCHOR WEST" over Bikaner (28.3, 73.6) | Extend airspace generator |
| CAP stations | NORTH (Jammu–Pathankot), CENTRAL (Amritsar–Ferozepur), SOUTH (Barmer–Bhuj) | Extend airspace generator |

### 4.11 Weather

Use the existing synthetic grid. Script two effects so the weather tool has something to say:
- Low cloud and reduced visibility over the Pir Panjal (lat 33.0–34.5, lon 74.0–75.5) on the night of 6–7 May. This lowers PGM-MOD IIR effectiveness and is a Monte Carlo sensitivity in Act 2.
- Dust haze over western Rajasthan on 9–10 May (flyability 0.6–0.7) for the Act 7 Pareto trade-off.

### 4.12 Missions (scripted, plus light background)

| Mission ids | Type | Priority | Window (IST) | Target / area | Depends on |
|---|---|---|---|---|---|
| M-S01..M-S09 | STRIKE (stand-off) | 1 | 7 May 01:05–01:30 TOT | T-S01..T-S09 | — |
| M-I01..M-I03 | ISR (BDA) | 2 | 7 May 01:45–04:00 | areas around P1 clusters | M-S* |
| M-A01..M-A02 | AAR | 2 | 7 May 00:00–02:30 | ANCHOR NORTH, ANCHOR WEST | — |
| M-D01..M-D03 | DCA (CAP) | 2 | continuous, rolling 4-hour windows | NORTH, CENTRAL, SOUTH | — |
| M-SD01 | SEAD | 1 | 8 May 07:00–08:00 | TH-LHR-01 / 02 | — |
| M-LM01..M-LM04 | SEAD (LM) | 1 | 9 May 02:30–04:00 | TH-AD-01..04 | — |
| M-B01..M-B08 | STRIKE | 1 | 10 May 02:00–05:00 | T-A01..T-A08 | M-SD* where applicable |
| background | mixed | 3–5 | random | random | — |

Missions need two new fields: `standoff_required: bool` (true for all STRIKE in this pack) and `roe_phase`. Background missions: set count to 10–15 (not 40–60) so the plan panel stays readable.

### 4.13 Doctrine documents for `search_doctrine` (synthetic text)

Write short, clearly fictional SOP files and index them into `doc_chunk`:

| doc_id | Content the demo quotes |
|---|---|
| `ROE-SINDOOR-P1` | "Phase 1 targets are limited to designated terrorist infrastructure. No military installation. Weapons release from own airspace. Collateral sensitivity above 0.7 requires a precision weapon with a CEP class of A." |
| `ROE-SINDOOR-P2` | "Hostile AD and radar that threaten own forces are authorised targets." |
| `ROE-SINDOOR-P3` | "Airbases, C2, and weapons storage are authorised targets." |
| `SOP-CUAS-01` | "Engage Class 1–2 UAS with soft-kill or guns first. Medium SAMs are reserved for manned aircraft and cruise missiles unless a defended asset of value ≥ 90 is at risk." |
| `SOP-SEAD-01` | "Strike packages do not route through an active long-range SAM engagement zone with confidence ≥ 0.6 without SEAD." |
| `ORDER-CEASE-01` | "Cessation of firing and military action on land, sea, and air effective 1700 IST 10 May. Defensive posture maintained." |

---

## 5. Data generation plan

### 5.1 Approach: a scripted scenario pack

The current generators are random by design (cluster centres drawn at lat 24–31.5, lon 69–72; hostile sites clamped west of `WEST_LON_MAX = 72.9`). That clamp alone would move Muridke, Sialkot, Kotli, and Lahore into the wrong place. The fix is a **scenario pack**: hand-authored YAML for anything the story depends on, random generation for everything else, all under one seed.

```
config/
  scenarios/
    sindoor.yaml                 # top-level: seed, start, horizon, bbox, fleet, pack path
    sindoor/
      roe.yaml                   # section 4.2
      bases.yaml                 # section 4.3 (B8–B14 additions)
      fleet_split.yaml           # section 4.4 home-base splits and forced states
      weapons_extra.yaml         # section 4.5
      targets.yaml               # section 4.6, all three phases
      threats.yaml               # fixed hostile AD and radar, appear times
      friendly_ad.yaml           # section 4.7 placement rules per site class
      defended_sites.yaml        # section 4.8
      raids.yaml                 # section 4.9 raid definitions (not tracks)
      airspace.yaml              # section 4.10 zones
      weather_overrides.yaml     # section 4.11
      missions.yaml              # section 4.12
      events.yaml                # section 6 event script
      doctrine/*.md              # section 4.13
```

`ScenarioConfig` gets `pack: Path | None`. When `pack` is set, `build_scenario` calls `load_pack(pack)` and each generator uses the scripted rows where the pack provides them.

| Generator | With pack |
|---|---|
| `targets.py` | Load `targets.yaml` instead of random draws |
| `threats.py` | Load `threats.yaml`; generate AAA and AIRFIELD point defences from template rules; skip `_cluster_centers` and the `WEST_LON_MAX` clamp; replace the clamp with a "point is on the Red side of the border polygon" check |
| `missions.py` | Load `missions.yaml`; add N background missions from the existing NHPP sampler |
| `aircraft.py`, `crew.py` | Random as today, then apply `fleet_split.yaml` homes and forced states |
| `weapons.py` | Merge `weapons_extra.yaml` into `_CATALOG`, recipes, and stocks |
| `airspace.py` | Existing generation plus `airspace.yaml` zones; generate the no-cross polygon from the border fixture |
| `weather.py` | Existing grid plus `weather_overrides.yaml` multipliers |
| `adsb.py` | Existing fixture plus the reroute tracks |
| new `friendly_ad.py` | Place units per site class rules |
| new `raids.py` | Build `AirTrack`s from `raids.yaml` (section 5.4) |
| new `engagements.py` | Resolve track fates against friendly AD (section 5.5) |

Commands:

```bash
make up
cd backend && PYTHONPATH=src python -m garuda.generate --config ../config/scenarios/sindoor.yaml --seed 2025 --dry-run
cd backend && PYTHONPATH=src python -m garuda.generate --config ../config/scenarios/sindoor.yaml --seed 2025
cd backend && PYTHONPATH=src python -m garuda.ingest.doctrine --dir ../config/scenarios/sindoor/doctrine
cd backend && PYTHONPATH=src python -m garuda.viz --config ../config/scenarios/sindoor.yaml --seed 2025 --out ../data/viz/sindoor.html
GARUDA_CONFIG=config/scenarios/sindoor.yaml GARUDA_SEED=2025 make api
```

Add a Makefile target `demo-sindoor` that runs the four commands. The doctrine ingest module does not exist yet (`storage/doctrine.py` only reads); add a small writer that chunks and embeds the markdown files.

### 5.2 Top-level config

```yaml
# config/scenarios/sindoor.yaml
seed: 2025
start: "2025-05-06T12:00:00Z"
horizon_hours: 98
bases: 14
pack: scenarios/sindoor
bbox: {lat_min: 22.5, lat_max: 35.5, lon_min: 66.5, lon_max: 78.5}
fleet: {Su-30MKI: 40, Rafale: 24, Tejas: 24, Mirage-2000: 18, Jaguar: 12, C-130J: 8, IL-78: 6, AEW: 4, Heli: 14}
crew_per_airframe: 2.0
background_missions: 12
events: []          # loaded from the pack's events.yaml
```

Check the cost of the larger bbox. The weather grid (0.5°) grows from about 440 cells to 650 per hour, and the terrain raster at 1 km grows by about 40%. Both are fine. The ADIZ and CAP generators read the border fixture; confirm they still clip correctly at the new bbox.

### 5.3 Example pack rows

```yaml
# targets.yaml
- {target_id: T-S08, name: "Markaz Taiba", near: Muridke, lat: 31.80, lon: 74.26,
   target_class: FACILITY, phase: P1, group: TERROR_INFRA,
   value: 100, hardness: 0.55, collateral_sensitivity: 0.8, aim_points: 3,
   defended_by: [TH-AAA-S08-1, TH-AAA-S08-2]}
- {target_id: T-A04, name: "Sargodha", lat: 32.05, lon: 72.67,
   target_class: AIRBASE, phase: P3, group: AIRBASE,
   value: 100, hardness: 0.85, collateral_sensitivity: 0.2, aim_points: 2,
   defended_by: [TH-AF-A04, TH-SAM-A04]}

# threats.yaml
- {threat_id: TH-LHR-01, type: SAM_LONG, lat: 31.52, lon: 74.40, mobile: false,
   engagement_range_km: 180, detection_range_km: 260, min_alt_ft: 1000, max_alt_ft: 80000,
   pk_base: 0.75, confidence: 0.85, appears_at_h: 36.5, status: ACTIVE}

# raids.yaml
- raid_id: R2
  start_h: 50.5
  end_h: 56.0
  total: 350
  mix: {UAS_ARMED: 0.60, UAS_DECOY: 0.25, LOITERING: 0.15}
  sites: all
  allocation: value_weighted        # more tracks at higher asset_value
  launch_depth_km: [15, 60]          # Red side of the border
  speed_kts: {UAS_ARMED: [60, 100], UAS_DECOY: [50, 80], LOITERING: [70, 110]}
  alt_ft: [300, 3000]
  wave_shape: three_pulses           # 20:00, 22:30, 00:45 IST

# events.yaml (see section 6 for the full list)
- {t: 30.5, type: raid, raid_id: R1}
- {t: 79.0, type: aircraft_nmc, entity_ids: [SU-0103, SU-0107], reason: hydraulic}
```

### 5.4 Raid track generation (`generators/raids.py`)

For each raid, deterministic under `stream_rng(seed, 201 + raid_index)`:

1. **Allocate tracks to sites.** `weight_i = asset_value_i ** 1.3`, multinomial over `total`, floor of 3 per site. For R2 this yields about 15–25 tracks at base sites and 4–8 at towns, about 350 in total across 36 sites.
2. **Pick a launch point.** Take the nearest point on the border to the site, then move it `U(15, 60)` km into the Red side along the outward normal, with ±25° jitter. Reject and redraw if the point is not inside the Red polygon.
3. **Pick a launch time.** `three_pulses`: a mixture of three normal distributions centred on the pulse times, σ = 20 min, clipped to the raid window.
4. **Fly the track.** Great-circle path to the site with 1–3 random doglegs (decoys use 3–5), 30 s samples, speed from the kind's band, altitude random-walk inside the band. Cruise missiles fly 100–300 ft at 450–500 kts with a terminal pop-up.
5. **Tag recovered debris.** Choose 6 `UAS_ARMED` tracks that will end `KILLED_KINETIC` over Indian territory and mark them `debris_recovered: true, debris_class: "SONGAR-class"`. This feeds the Act 5 debris marker.

### 5.5 Engagement resolution (`generators/engagements.py`)

Resolve fates once at generation time so the map, the database, and the tools agree:

```
for each sample time t (30 s), for each live track:
    candidates = friendly AD units with status ACTIVE whose envelope contains the track
    order candidates by doctrine: CUAS-SOFT, GUN-LL, CUAS-HARD, SAM-SR, SAM-MR-P, SAM-MR-A
        (SOP-CUAS-01: skip SAM-MR-* for UAS unless site.asset_value >= 90)
    pick the first candidate with a free channel and rounds > 0
    draw Bernoulli(pk_vs[track.kind]); decrement rounds; occupy the channel for reload_min
    hit and SOFT -> KILLED_SOFT; hit and KINETIC -> KILLED_KINETIC
    track reaches the site -> LEAKED
```

Tune the fictional Pk values so R2 ends near 85–92% killed, 3–5% leakers, and the remainder lost or decoys. Write the per-unit round consumption to a time series. That gives `forecast_availability`-style "interceptor stock over time" data for Act 5.

### 5.6 BDA and phase outcomes (synthetic)

| Event | Outcome rows |
|---|---|
| Act 2 BDA (T+9) | All nine P1 targets `HIT`, BDA confidence 0.9 for eight, 0.55 for T-S04 (cloud), which triggers an ISR re-look |
| Act 4 (T+38.5) | TH-LHR-01 `SUPPRESSED`, confidence decays per `confidence_at` |
| Act 6 (T+58) | TH-AD-04 `DESTROYED`; TH-AD-01..03 `SUPPRESSED` |
| Act 7 (T+84) | Airbase rows `DAMAGED` with runway-closure hours |

These are written by the scripted event handler when the clock passes the event, not at seed time, so the "before" state is visible on screen.

### 5.7 Data validation checks (add to `backend/tests/test_sindoor_pack.py`)

- All P1 targets lie on the Red side of the border, and no P1 target is a military class.
- Every Blue base and defended site lies on the Blue side.
- `TH-LHR-01` engagement ring intersects the B11 Amritsar base point (the story needs it).
- T-S09 Bahawalpur is more than 60 km from the nearest Blue-side point (so `PGM-MOD` is infeasible and `CM-STANDOFF-L` is forced).
- T-A08 Bholari is beyond `CM-STANDOFF-L` reach from any Blue launch basket, so `CM-SUPERSONIC` is required.
- R2 has 300–400 tracks across exactly 36 sites. Fates sum to the total. Killed share is 85–92%.
- At T0, at least 14 Rafale at B4 are FMC; at T+79, the two named Su-30 tails go NMC.
- The generator is deterministic: same seed gives an identical `scenario_counts` and identical track hashes.

---

## 6. Event script

Driven by a new endpoint `POST /api/scenario/clock` (`{"action": "jump", "to_h": 30.5}` or `play` / `pause` / `speed`). The endpoint is designed in `docs/02_llm_harness/api_design.md` but not built. When the clock passes an event time, the handler applies it and emits an SSE `scenario_event` that the UI renders as a toast plus a timeline marker.

| T+ (h) | IST | Event type | Payload | Exists? |
|---|---|---|---|---|
| 0 | 6 May 17:30 | `roe_phase` | P1 | New |
| 6.0 | 6 May 23:30 | `notam` | civil closures active | New |
| 7.6–8.0 | 7 May 01:05–01:30 | (plan execution) | weapons impact at assigned TOT | New (playback) |
| 9.0 | 7 May 02:30 | `bda_update` | section 5.6 | New |
| 30.5 | 8 May 00:00 | `raid` | R1 | New |
| 36.0 | 8 May 05:30 | `roe_phase` | P2 | New |
| 36.5 | 8 May 06:00 | `new_sam` | TH-LHR-01, TH-LHR-02 appear | Exists (`new_sam`), extend for named ids |
| 38.5 | 8 May 08:00 | `bda_update` | TH-LHR-01 suppressed | New |
| 50.5 | 8 May 20:00 | `raid` | R2 | New |
| 54.0 | 8 May 23:30 | `debris_recovered` | SONGAR-class tags | New |
| 57.0 | 9 May 02:30 | (LM execution) | | |
| 58.0 | 9 May 03:30 | `bda_update` | TH-AD-04 destroyed | New |
| 75.5 | 9 May 21:00 | `raid` + `roe_phase` | R3, P3 | New |
| 79.0 | 10 May 00:30 | `aircraft_nmc` | SU-0103, SU-0107 at B1 | Exists |
| 84.0 | 10 May 05:30 | `bda_update` | airbases damaged | New |
| 95.5 | 10 May 17:00 | `ceasefire` + `roe_phase` | P4 | New |

`EventType` in `contracts/events.py` needs `raid`, `roe_phase`, `bda_update`, `notam`, `debris_recovered`, `ceasefire`. `retask_rolling_horizon` needs to understand `roe_phase` and `ceasefire` (cancel offensive missions after the effective time), and `raid` (treated like `p1_request` for DCA).

---

## 7. Act-by-act script: prompts, expected app behaviour, map

Each act gives the clock action, the exact prompt to type, the expected agent behaviour (mode, steps, tools), what the panels show, and what the map shows. Prompts are written to exercise the main agent's standard operating procedures in `harness/main/prompts.py`. Keep the wording exact on recording day so cached runs match.

### Act 0: Cold open (T+0, 6 May 17:30 IST)

- **Clock**: reset to T+0.
- **Voice-over**: "22 April, Pahalgam. Twenty-six killed. Two weeks later, the planning staff has nine targets, a narrow ROE, and a few hours. This is GARUDA running a synthetic reconstruction of that problem."
- **Map**: fit to the full bbox. Layers on: IB/LoC, Blue bases, civil traffic, weather. Slow pan from Leh to Sir Creek. Toggle the "BLUE NO-CROSS (ROE)" hatch on, then the friendly AD rings.
- **On-screen caption**: the data-policy line from the top of this document.

### Act 1: Intelligence brief (T+0)

**Prompt 1.1 (agent mode)**

```
Brief me on the current picture. List the nine designated terrorist-infrastructure targets with their nearest town, distance from the IB or LoC, and any hostile air defence covering each one. Also tell me how many Rafale at Ambala are fully mission capable right now.
```

- **Expected behaviour**: `choose_mode` → agent. Calls retrieval (`get_mission_requests` for M-S01..09 and their targets, `get_threats_in_area` around each target, `get_aircraft_availability` base B4 type Rafale status FMC) and spatial (`distance_km` from each target to the border). New data tool needed: `get_targets` (there is no target lookup today; targets are only reachable through missions or ontology).
- **Chat answer**: a nine-row table (id, name, town, border distance km, defending threats), then "N Rafale FMC at B4", each figure citing its source.
- **Map**: fit to the northern and Bahawalpur area. Nine red numbered diamonds appear in sequence. Dashed grey lines to the nearest border point with km labels (Sarjal about 6 km, Bahawalpur about 100 km). Small AAA rings at each site. Rafale count badge on the Ambala icon.

**Prompt 1.2 (agent mode, shows doctrine retrieval)**

```
What does the Phase 1 ROE allow and forbid?
```

- **Expected**: `search_doctrine` returns `ROE-SINDOOR-P1`. The answer quotes it and cites the doc id.
- **Map**: no change. The ROE badge pulses.

### Act 2: Stand-off strike plan (T+0.5, 6 May 18:00 IST)

**Prompt 2.1 (plan mode)**

```
Build a strike plan for all nine Phase 1 targets with time on target between 01:05 and 01:30 IST on 7 May. Constraints: no aircraft crosses the IB or LoC, stand-off weapons only, no military installation is targeted, keep collateral risk low on the high-sensitivity sites, and keep Rafale for the deepest targets. Include tanker support and deconflict with civil traffic.
```

- **Expected behaviour**: `choose_mode` → plan. Discovery calls retrieval and spatial. Then `propose_plan`:

| Step | Agent | Objective | Success criteria |
|---|---|---|---|
| s1 | retrieval | Pending P1 missions M-S01..M-S09, targets, stocks of CM-STANDOFF-L and PGM-MOD at B4 | all calls ok |
| s2 | assign_weapons_to_targets | Allocate stand-off weapons to T-S01..T-S09 with min Pk per priority, ROE P1 filter | every target Pk ≥ 0.80 |
| s3 | assign_missions | Aircraft and load configs for M-S01..09, AAR M-A01..02, DCA M-D01..03, using s2 allocation | status done, plan_id present, no P1 unassigned |
| s4 | plan_route | Route each package to its launch basket, no-cross zone blocked | cumulative risk ≤ 0.1 |
| s5 | schedule_crew | Crew for plan_id from s3 | no unfilled slots |
| s6 | schedule_tanker_aar | Tanker meetings for plan_id | no unserved requests |
| s7 | deconflict_airspace | include_civil true | no remaining conflicts |
| s8 | simulation | Monte Carlo, 5000 runs | report P(value ≥ threshold) |

- **Commander action**: read the work plan aloud, click **Approve work plan**. This approves running sub-agents only, not tasking.
- **Step progress**: the chat shows each step running, then done, with timings. Record the real timings; they are a KPI.
- **Plan panel, final**: assignment table

| Target | Weapon (alias) | Package | Base | Launch basket | Release time (IST) | TOT (IST) | Pk |
|---|---|---|---|---|---|---|---|
| T-S09 Bahawalpur | CM-STANDOFF-L ×2 (SCALP-class) | Rafale ×2 | B4 | LB over Sri Ganganagar | from solver | 01:08–01:28 | from solver |
| … | … | … | … | … | … | … | … |

  plus KPIs (total value, fulfilment, sorties, stand-off weapons used vs stock, mean route risk) and the validator panel. The validator should show **zero** ROE or border conflicts. If a civil conflict remains, s7 resolves it, and the diff of minutes shifted is shown.
- **Map, revealed step by step as outputs arrive**:
  1. s2: yellow dashed flyout lines appear from the Indian side to each target.
  2. s4: green launch baskets appear (all inside India, outside the 10 km buffer). Blue routes draw from Ambala and Adampur to the baskets.
  3. s6: cyan tanker racetracks over Punjab, with meeting points.
  4. s7: some route ETAs change; changed segments flash.
  5. Timeline: nine TOT bars stacked inside the 01:05–01:30 window.
- **Weapon choice the demo should visibly show**: PGM-MOD for the near-border sites (Sarjal, Mehmoona Joya, Barnala, Kotli sites), CM-STANDOFF-L for Bahawalpur and Muridke (high value, high collateral sensitivity), and either for Muzaffarabad depending on stock. Point at this on camera: the solver chose the weapon from range, stock, Pk, and collateral sensitivity together.

**Prompt 2.2 (plan mode, robustness)**

```
How robust is this plan? Compare the baseline with heavy threat and degraded weather, and tell me the weakest link.
```

- **Expected**: simulation step (Monte Carlo) three times: none, `heavy_threat`, `degraded_weather`.
- **Plan panel**: three value distributions, P(value ≥ threshold), expected losses (should be near zero, because aircraft never enter engagement zones), CVaR. Weakest link likely the PGM-MOD shots in the Pir Panjal cloud (section 4.11).
- **Map**: the weakest-link targets pulse amber.

**Commander action**: "Approve plan." The real commit happens through the approval API; GARUDA states it does not commit plans itself.

### Act 2b: Execution and BDA (T+7.6 → T+9)

- **Clock**: jump to 7 May 00:45 IST, play at 60×.
- **Map**: aircraft icons move along routes, meet tankers, hold in baskets. At release, yellow flyout dashes animate across the border. Diamonds flash at TOT. Civil traffic is visibly south and clear.
- At T+9 the `bda_update` toast appears.

**Prompt 2b.1 (agent mode)**

```
Give me the battle damage assessment for the nine targets and flag any where confidence is too low to close.
```

- **Expected**: nine rows, T-S04 flagged at 0.55 confidence; recommends ISR re-look M-I0x. Diamonds turn to struck icons; T-S04 gets an amber "?".

### Act 3: Threat to Indian stations (T+30.5, 8 May 00:00 IST)

- **Clock**: jump to T+30.5. Raid R1 starts. Red tracks stream from the west toward eight stations. Engagements pop as X and bolt icons.

**Prompt 3.1 (agent mode)**

```
Hostile drones and missiles are inbound on several stations. Which of our bases are under attack, what air-defence cover does each have, and where are the gaps?
```

- **Expected**: retrieval `get_threats_in_area` (needs to return air tracks too, or a new `get_air_tracks` tool), spatial `within_radius` per base, friendly AD lookup (new `get_air_defence_status` tool).
- **Answer**: eight stations, track counts, AD layers present, gaps (for example Bhuj has no medium SAM, Chandigarh has thin gun coverage).
- **Map**: the eight station icons get red attack halos. Gap areas highlight as red-tinted sectors between AD rings.

**Prompt 3.2 (plan mode, first retask)**

```
Retask to put DCA CAPs over the northern and central sectors within 30 minutes without dropping any committed missions. Use fresh aircraft and crew only.
```

- **Expected**: SOP "Retask after an event": retrieval confirms the event and plan_id, then `retask_rolling_horizon` with event `raid`, then `schedule_crew`, `deconflict_airspace`, Monte Carlo on new_plan_id.
- **Plan panel**: the plan **diff** (added DCA sorties, unchanged missions, nothing dropped), fatigue reasons for rejected crew.
- **Map**: blue CAP racetracks appear over Jammu–Pathankot and Amritsar–Ferozepur with on-station times.

### Act 4: ROE Phase 2 and SEAD near Lahore (T+36.5, 8 May 06:00 IST)

- **Clock**: jump to T+36.0. ROE badge changes to **P2: + HOSTILE AD**. At T+36.5, TH-LHR-01's big red ring appears, covering Amritsar and the northern routes. The `new_sam` toast shows.

**Prompt 4.1 (plan mode)**

```
A long-range SAM and an EW radar just appeared near Lahore, and their coverage reaches Amritsar. Phase 2 ROE now allows hostile air defence and radar. Plan a SEAD mission against both, then re-route any Blue package that crosses the new engagement ring. Aircraft still do not cross the IB or LoC.
```

- **Expected behaviour**: discovery confirms the threat ids and the current plan_id. Work plan:

| Step | Agent | Objective | Success criteria |
|---|---|---|---|
| s1 | retrieval | TH-LHR-01 and TH-LHR-02, missions whose routes intersect the 180 km ring | ids copied verbatim |
| s2 | search via retrieval | Quote `SOP-SEAD-01` and `ROE-SINDOOR-P2` | doc ids cited |
| s3 | plan_route | Two routes from B5 Adampur to the launch basket for TH-LHR-01: one ignoring the ring, one with the ring blocked | both routes returned, risk numbers differ |
| s4 | assign_weapons_to_targets | ARM-SEAD and CM-STANDOFF-L against TH-LHR-01 and TH-LHR-02, ROE P2 | both targets Pk ≥ 0.70 |
| s5 | retask_rolling_horizon | Add M-SD01, shift any sortie whose route enters the ring | new_plan_id, diff lists only the affected sorties |
| s6 | deconflict_airspace | Clear the new times | no remaining conflicts |

- **The beat to point at**: the two routes side by side. The direct route is shorter and red (cumulative risk above the cap). The blocked route is longer, stays in India, and is green. The planner says the risk number, not "safer".
- **Map**: fit to Lahore–Amritsar–Adampur. The 180 km engagement ring and the 260 km detection ring draw in. The unsafe route flashes red, then the safe route draws in blue. A yellow flyout from a basket west of Amritsar reaches TH-LHR-01 without the aircraft icon leaving India. After the BDA event at T+38.5 the ring turns grey and the label changes to SUPPRESSED.

### Act 5: The large drone wave (T+50.5, 8 May 20:00 IST)

- **Clock**: jump to 8 May 19:50 IST, then play at 30× through the first pulse. Three pulses are scripted at 20:00, 22:30, and 00:45 IST.
- **Map**: zoom out to Leh–Sir Creek. About 350 red triangles stream in from the Red side toward all 36 defended sites. Kinetic kills are red X, soft kills are purple bolts, leakers are orange "!". Defended-site badges count inbound tracks. Blue medium-SAM rings stay dark except over sites with asset value ≥ 90, which is the doctrine rule made visible.

**Prompt 5.1 (agent mode)**

```
How many hostile air tracks are we seeing, across how many sites, from where to where? Break them down by kind, and tell me how many we have killed with guns or soft-kill versus medium SAMs. Flag any site that is running out of interceptors.
```

- **Expected behaviour**: agent mode. New tools `get_air_tracks` (filter by raid, time window, kind, fate) and `get_air_defence_status` (rounds remaining, channels busy, shots by system class). `detect_threat_anomalies` flags the three pulses as a rate spike against the R1 baseline.
- **Chat answer**: one number for tracks (inside 300–400), "36 sites, Leh to Sir Creek", the mix (armed, decoy, loitering), kill split by system class, and the two or three units whose `rounds` are below 25%. Every figure cites the tool call.
- **Map**: the anomaly toast marks the pulse times on the timeline. Units low on rounds get an amber badge.

**Prompt 5.2 (plan mode)**

```
Allocate counter-UAS for the rest of the night. Follow doctrine: soft-kill and guns first, keep medium SAMs for cruise missiles and for sites with asset value of 90 or above. Show me which sites still have a gap after that allocation.
```

- **Expected behaviour**: retrieval quotes `SOP-CUAS-01`. A new optimizer, `allocate_counter_uas`, assigns channels and rounds to live tracks under the doctrine ordering from section 5.5. It returns served tracks, unserved tracks, rounds spent per class, and sites left with a gap.
- **Plan panel**: a bar of shots by system class (guns and soft-kill dominate; Akash-class and Pechora-class are a thin slice) and a short gap list.
- **Map**: gap sites pulse. A small chart in the plan panel shows interceptor stock falling across the three pulses and flattening after the allocation.

**Prompt 5.3 (agent mode)**

```
What did we recover from the drones we shot down over our own territory?
```

- **Expected**: the six tracks tagged `debris_recovered: true, debris_class: "SONGAR-class"`. The answer lists the six sites and says the classification is preliminary and synthetic.
- **Map**: six debris pins drop on the Blue side with a popup "recovered debris, SONGAR-class (synthetic classification)".

### Act 6: Armed drones against four air-defence sites (T+57, 9 May 02:30 IST)

**Prompt 6.1 (plan mode)**

```
Launch armed drones from the ground launcher near Pathankot against the four hostile air-defence sites that are still active. Prioritise the radar. Aircraft stay on the ground for this one.
```

- **Expected behaviour**: `assign_weapons_to_targets` with weapon `LM-ARM`, stock at `GL-1`, targets TH-AD-01..03 and TH-AD-04. The radar (TH-AD-04) gets the first rounds because its value is highest and it has no Pk of its own. `plan_route` is not used; these are one-way munitions, so the map draws flyouts only.
- **Plan panel**: four rows, rounds allocated, expected Pk, rounds left at GL-1 (12 is the stock, so the panel shows scarcity).
- **Map**: fit to Sialkot–Okara–Bahawalnagar. Four yellow flyouts leave GL-1 and cross the border. At the T+58 BDA event, TH-AD-04 turns to a black X (DESTROYED) and the other three rings turn grey (SUPPRESSED).

### Act 7: Airbase strikes, then an aircraft goes down (T+75.5, 9 May 21:00 IST)

- **Clock**: jump to T+75.5. ROE badge changes to **P3: + AIRBASES, C2, STORAGE**. Raid R3 starts in the north and fades; do not dwell on it.

**Prompt 7.1 (plan mode)**

```
Phase 3 is in effect. Build a strike plan for the eight airbases: Noor Khan, Rafiqui, Murid, Sargodha, Rahim Yar Khan, Jacobabad, Sukkur, and Bholari. Time on target between 02:00 and 05:00 IST on 10 May. Stand-off weapons only, no aircraft crosses the border. Give me two or three options that trade mission value against route risk and weapons used, and recommend one.
```

- **Expected behaviour**: the new-plan SOP, then `pareto_tradeoffs` because the commander asked for options.

| Step | Agent | Objective |
|---|---|---|
| s1 | retrieval | M-B01..M-B08, stocks of CM-STANDOFF-L and CM-SUPERSONIC, FMC Su-30 at B1 and B12, FMC Rafale at B4 |
| s2 | assign_weapons_to_targets | Both weapon types, ROE P3, min Pk 0.80 |
| s3 | assign_missions | Packages for the eight missions |
| s4 | plan_route | Launch baskets on the Blue side; Bholari is the long one |
| s5 | schedule_tanker_aar | IL-78 for the deep Su-30 sortie |
| s6 | pareto_tradeoffs | Variants on value, risk, and munitions; return a recommendation |

- **The beat to point at**: Bholari. Its distance from every Blue basket is beyond `CM-STANDOFF-L` (250 km) and inside `CM-SUPERSONIC` (300 km), so the assignment is forced. Say that on camera: the solver had one feasible weapon, and the table shows it.
- **Plan panel**: a small Pareto chart (value vs risk, three dots, the recommended one outlined) and the assignment table. The recommendation cites the step id.
- **Map**: eight purple runway icons. Blue routes and yellow flyouts. The Bholari flyout is visibly the longest, leaving from the Jaisalmer–Barmer basket. Cyan tanker track for that package.

**Prompt 7.2 (plan mode, the retask)**

- **Clock**: jump to T+79 (10 May 00:30 IST). Toast: "SU-0103 and SU-0107 at Jodhpur are NMC, hydraulic."

```
Two Su-30s at Jodhpur just went unserviceable, and they were in the airbase plan. Repair the plan with the smallest possible change. Do not rebuild it, and do not drop Bholari.
```

- **Expected behaviour**: the retask SOP. The prompt says "do not rebuild", which matches the rule that a repair goes to `retask_rolling_horizon` and not `assign_missions`. Event: `aircraft_nmc`, entity ids SU-0103 and SU-0107, time T+79.

| Step | Agent | What it must show |
|---|---|---|
| s1 | ontology | `plan_assignments` for the current plan_id, so the two tails are confirmed on missions |
| s2 | retask_rolling_horizon | new_plan_id, a diff of two or three rows, Bholari still assigned |
| s3 | schedule_crew | replacement crews; if a slot stays empty, the reason code is on screen |
| s4 | simulation | Monte Carlo on the new plan against the old plan |

- **Plan panel**: a diff table. Removed tails, replacement tails, the mission that moved base (likely from B1 to B12 Bathinda), minutes of TOT shift, and the value KPI before and after. The delta should be small. That is the point of the act.
- **Map**: the two Jodhpur tails grey out. The replacement route draws from Bathinda. Changed segments flash for two seconds. Unchanged routes stay still, so the "minimal change" is visible.

### Act 8: Cessation (T+95.5, 10 May 17:00 IST)

- **Clock**: jump to 10 May 16:50 IST. At 17:00 the `ceasefire` event fires. ROE badge changes to **P4: DEFENSIVE**. Offensive mission bars on the timeline grey out from 17:00 onward.

**Prompt 8.1 (plan mode)**

```
Cessation of firing and military action is effective 1700 IST today, on land, at sea, and in the air. Cancel every offensive mission that has not yet launched, keep the defensive CAPs and the air-defence posture, and tell me what is still tasked after 1700.
```

- **Expected behaviour**: retrieval quotes `ORDER-CEASE-01`. `retask_rolling_horizon` with event `ceasefire` at T+95.5. Offensive missions with TOT after 17:00 become CANCELLED. DCA and friendly AD stay.
- **Chat answer**: a short posture list (CAP stations still manned, AD units still active, zero strike sorties after 17:00) with the new_plan_id. The closing line states that committing the change is the commander's action.
- **Map**: strike routes and flyouts fade. CAP racetracks and blue AD rings remain. The no-cross hatch stays, which reads well: the constraint never changed, only the tasking did.

### Close (0:30)

Leave the plan panel on a recap the last answer already produced. Voice-over, one sentence per line:

- Nine targets planned, weapons and routes chosen against stock, range, Pk, and collateral sensitivity.
- The picture changed four times: inbound raid, a new SAM, a swarm across 36 sites, two aircraft down. Each time the plan was repaired instead of rebuilt.
- Every option came with a risk number, and every commit waited for the commander.

End on the section 17 formula, as a caption: **Aircraft + Crew + Weapon + Target + Route + Threat + Air Defence + Weather + Time + Mission Priority.**

---

## 8. What the app has to gain before this can be recorded

The backend generates a western-theatre scenario and answers through the main agent. The demo needs a scenario pack, a few new domains, and a frontend. Build in this order so each stage is recordable on its own.

### Stage A: data (no UI yet)

1. Scenario pack loader (`pack:` on `ScenarioConfig`) and `config/scenarios/sindoor.yaml` plus the YAML in section 5.1.
2. Schema additions: `Target.name`, `phase`, `group`, `collateral_sensitivity`, `aim_points`; new target classes `FACILITY`, `AD_SITE`, `AIRBASE`, `AMMO_DEPOT`; `WeaponType.display_name`; `Mission.standoff_required` and `Mission.roe_phase`.
3. `FriendlyAdUnit`, `DefendedSite`, `AirTrack`, and `BdaReport` models, generators (`friendly_ad.py`, `raids.py`, `engagements.py`), and Postgres tables with an Alembic revision.
4. Base list to 14, fleet home splits, weapon rows in section 4.5, station-limit bump for the Su-30 centreline.
5. `make demo-sindoor` and `backend/tests/test_sindoor_pack.py` (the checks in section 5.7).
6. Doctrine markdown indexed into `doc_chunk`. This needs a writer; `storage/doctrine.py` only reads today.

Exit test: `generate --dry-run` prints stable counts, and the viz page shows the nine targets, the Lahore ring, and the 36 sites on the map.

### Stage B: tools and events

| Addition | Why the demo needs it |
|---|---|
| `get_targets` | Act 1 table. Targets are not queryable on their own today. |
| `get_air_tracks` | Acts 3 and 5. `get_threats_in_area` returns ground threats only. |
| `get_air_defence_status` | Acts 3 and 5. Friendly AD does not exist in the tool catalog. |
| `get_bda` | Act 2b and the later BDA toasts. |
| `allocate_counter_uas` | Act 5. New small optimizer: assign channels and rounds under `SOP-CUAS-01`, return gaps and rounds spent. |
| ROE filter inside `assign_missions`, `assign_weapons_to_targets`, and `validation/rules.py` | Acts 2, 4, 7. A P1 plan that targets an airbase must come back as a validator conflict. |
| Launch-basket output on `plan_route` | Act 2 map. A basket is the release point: inside Blue airspace, outside the 10 km buffer, inside weapon range of the target. |
| `retask_rolling_horizon` accepts `raid`, `roe_phase`, `ceasefire` | Acts 3, 7, 8. Today the event enum is `aircraft_nmc`, `new_sam`, `base_closed`, `p1_request`, `tanker_lost`, `weather_override`. |
| `POST /api/scenario/clock` and `POST /api/scenario/inject-event` | The clock is the spine of the recording. Both are specified in `docs/02_llm_harness/api_design.md` and neither exists. Jumping the clock applies every event with `t` at or before the new time and emits `scenario_event` on the SSE stream. |
| `GET /api/scenario` returns the pack layers | The current payload (bases, threats, targets, routes, weather, ADS-B) needs defended sites, friendly AD, air tracks, launch baskets, and BDA state. |

Exit test: a scripted chat test (no LLM, the pattern in `backend/tests/test_api.py`) runs prompts 1.1, 2.1, and 7.2 against the pack and asserts the plan diff and the ROE validator.

### Stage C: the one-page UI

The layout in section 3. There is no `frontend/` directory; the Makefile `web` target already assumes one.

- Map: reuse the layer styling in `backend/src/garuda/viz_template.html` (Leaflet, CARTO tiles) and add the layers in section 3.1. Fit bounds per act.
- Chat: consume the SSE frames from `/api/chat` (`session`, step events, `done`). Render the work-plan approval card and post to `/api/chat/{session_id}/resume`.
- Plan panel: assignments, KPIs, validator conflicts, and the plan diff (`added`, `removed`, `shifted`).
- Timeline: phase bands, event markers, mission bars, a now-line bound to the clock.
- Clock controls call `/api/scenario/clock`.

Exit test: click through Acts 0–8 against the seeded stack with the model stubbed, and confirm every map layer in section 3.1 toggles and every approval button resumes the session.

### Stage D: rehearsal with the live model

Run the exact prompts in section 7 twice. Save both transcripts. Differences to fix before recording:

- The agent picks `assign_missions` for a repair. Tighten the prompt text; the SOP already forbids this, so the failure is usually a vague user prompt. The prompts above are written to prevent it.
- A sub-agent asks a question the commander must answer. Pre-write the answer (horizon, plan_id, event time) and keep it on a card next to the keyboard.
- A step returns `partial`. Section 6 of the master prompt lists the remedy for each partial; decide before recording whether to show the remedy or to cut.

---

## 9. Acceptance checks for the recording

These are the moments a viewer can verify. If one fails in rehearsal, fix the data, not the narration.

| Check | Act | Pass condition |
|---|---|---|
| Nine targets, right towns | 1 | The table names all nine from `SINDORE.MD` section 3 |
| Border distances | 1 | Bahawalpur is the long one, Sarjal is the short one |
| Doctrine quote | 1 | The answer cites `ROE-SINDOOR-P1` |
| No aircraft crosses | 2 | Every route vertex is on the Blue side of the border |
| Weapon fits the geometry | 2 | Bahawalpur gets `CM-STANDOFF-L`, a near-border site gets `PGM-MOD` |
| Stock is respected | 2 | Rounds used ≤ rounds on hand at B4 |
| Validator is clean | 2 | Zero ROE conflicts, zero border conflicts |
| Robustness has three cases | 2 | Baseline, heavy threat, degraded weather, plus a named weakest link |
| BDA flags the low-confidence site | 2b | T-S04 is the one below 0.7 |
| Eight stations | 3 | Jammu, Srinagar, Pathankot, Amritsar, Adampur, Bathinda, Chandigarh, Bhuj |
| Retask is a diff | 3, 7 | The panel shows a diff against the previous plan_id, not a fresh plan |
| Lahore ring matters | 4 | The ring covers Amritsar; the two routes have different risk numbers |
| Swarm scale | 5 | Track count is between 300 and 400, site count is 36 |
| Doctrine is visible | 5 | Most kills are guns or soft-kill; medium SAMs are the minority |
| Debris | 5 | Six SONGAR-class pins, all on the Blue side |
| Radar first | 6 | TH-AD-04 is the destroyed one; the other three are suppressed |
| Bholari is forced | 7 | Only `CM-SUPERSONIC` is feasible, and the table says so |
| Two tails, minimal change | 7 | SU-0103 and SU-0107 leave the plan; Bholari stays; the value KPI barely moves |
| Cessation | 8 | No strike sortie has a TOT after 17:00 IST on 10 May |
| The commander commits | all | Every plan appears first as a proposal with an Approve button |

---

## 10. Recording

### Setup

- Seed `2025`, config `config/scenarios/sindoor.yaml`. Reset the database and re-seed the morning of the recording so the ids on screen match the rehearsal transcript.
- Browser zoom 110%, 1920×1080, one display. Hide bookmarks. Cursor large.
- Pre-open the app at T+0 with layers IB/LoC, bases, and the no-cross hatch. Chat empty.
- Have the prompts in a local note, not in the app, and paste them. Typing on camera wastes time and invites typos that change the agent's behaviour.
- Second machine records the scenario clock and the API log, so a bad take can be matched to the exact tool calls afterwards.

### Shot list

| Take | Acts | Length | Notes |
|---|---|---|---|
| A | 0, 1 | 1:40 | One take. The pan and the nine-row table. |
| B | 2, 2b | 3:10 | Approve the work plan on camera. Cut the wait between steps only if a step exceeds 20 s; keep the step labels. |
| C | 3, 4 | 2:30 | The raid playback and the two-route comparison. |
| D | 5, 6 | 2:30 | Zoom out for the swarm, zoom in for the four flyouts. |
| E | 7, 8, close | 3:15 | The diff table is the frame to hold. |

Three-minute highlight, if a short cut is needed: Act 0 (last 15 s), Act 2 from the approval click to the route reveal, Act 5 prompt 5.1, Act 7 prompt 7.2 held on the diff.

### What the narration must say

- The reconstruction uses public geography and the public timeline. Force numbers, performance, stocks, and outcomes are synthetic.
- Weapon names on screen are class aliases. The data rows use generic codes.
- GARUDA proposes. The commander approves. Say this at the first approval and at the close, not in every act.

### What the narration must not say

- Do not state a real Pk, a real stock, a real tail number, or a real damage result. If a figure is on screen it came from the synthetic run; read it from the panel.
- Do not claim the routes or the aim points are the ones flown. They are a planner's reconstruction on approximate coordinates.
- Do not let the agent invent an id. If an answer contains an id that is not in the tool output, cut and re-roll that take.

---

## 11. File map

| Path | Role |
|---|---|
| `docs/SINDORE.MD` | Source narrative. The demo covers sections 3–15 and proves section 17. |
| `docs/04_demo/operation_sindoor_demo.md` | This plan. |
| `config/scenarios/sindoor.yaml` | Scenario entry point (to be written). |
| `config/scenarios/sindoor/` | The pack: ROE, bases, fleet split, weapons, targets, threats, friendly AD, sites, raids, airspace, weather, missions, events, doctrine. |
| `backend/src/garuda/generators/raids.py`, `engagements.py`, `friendly_ad.py` | New generators. |
| `backend/tests/test_sindoor_pack.py` | The checks in section 5.7. |
| `backend/src/garuda/api/main.py` | Gains the clock and the extra scenario layers. |
| `frontend/` | The one-page layout in section 3. |
