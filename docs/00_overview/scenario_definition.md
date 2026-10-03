# Scenario Definition (Fictional)

Theatre: Western air front (Rajasthan / Gujarat / Punjab area). All units, strengths, and threats are **fictional**; only geography and airfield locations use public data.

## Bases (7)
| base_id | Name (public airfield) | ICAO | Role |
|---|---|---|---|
| B1 | Jodhpur | VIJO | Fighter / strike |
| B2 | Jaisalmer | VIJR | Forward fighter |
| B3 | Bikaner (Nal) | VIBK | Forward fighter |
| B4 | Ambala | VIAM | Fighter / strike |
| B5 | Adampur | VIAX | Fighter |
| B6 | Bhuj | VABJ | Transport / CAS |
| B7 | Agra | VIAG | Tanker / transport / AWACS |

## Force size
~150 aircraft (Su-30MKI 40, Rafale 24, Tejas 24, Mirage-2000 18, Jaguar 12, C-130J 8, IL-78 tanker 6, AWACS/AEW 4, helicopters 14), ~300 crew.

## Timeline
- T0: 72-hour planning horizon, 6-hour planning buckets, 1-hour re-plan granularity.
- Scheduled events injected: aircraft NMC (T+6h), new SAM detected (T+14h), fog closes B5 (T+20h), priority-1 CAS request (T+30h), tanker loss (T+40h).

## Assumptions
- Hostile threats are generic ("Red") systems with fictional parameters.
- Weapons are generic classes (e.g. BVR-AAM, LGB-500, ASM) with fictional performance.
- Weather real (Open-Meteo/METAR); ADS-B real; everything else synthetic.
- Random seed fixed (`GARUDA_SEED`) for reproducibility.
