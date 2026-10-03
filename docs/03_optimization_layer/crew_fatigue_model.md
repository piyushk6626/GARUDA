# Crew Fatigue Model

Tool: `assess_crew_fatigue`

## Problem
Quantify fatigue (0 = fresh, 1 = unfit) of each crew member now and under a proposed schedule, to inform assignment and safety limits.

## Model (two parts)
1. **Biomathematical (SAFTE/FAST-inspired, simplified)**
   - Homeostatic sleep reservoir R(t): depletes while awake at rate `a`, recovers during sleep at rate `b` (restoration proportional to deficit).
   - Circadian term `C(t) = cos(2π (t − φ)/24)` with φ ≈ 17 h peak.
   - Sleep inertia and time-on-task term.
   - Effectiveness `E(t) = R(t)/R_max · (1 + c·C(t)) − ToT(t)`; `fatigue = 1 − clip(E, 0, 1)`.
   - Parameters from published literature defaults (documented as approximate; not validated for operational use).
2. **ML calibration**: gradient-boosted regressor learning residual to a synthetic "ground truth" (reaction time proxy) generated from the biomathematical model + noise and night/sortie-load effects; useful to include categorical factors (mission type, time zone shifts). Optional in the prototype.

## Inputs
Duty log (start/end), sleep opportunity (rest windows, assume 6.5 h sleep efficiency in rest block), flight hours last 7 d, night sorties, time awake, planned sorties.

## Pseudocode
```
state = R0
for each hour in history+plan:
  if asleep: R += b*(Rmax - R) else: R -= a
  E = R/Rmax*(1+c*cos(2pi*(h-phi)/24)) - tot_penalty
fatigue_end_of_duty = 1 - clip(E)
```

## Tool signature
```python
class FatigueInput(BaseModel):
    crew_ids: list[str] | None = None
    base_ids: list[str] | None = None
    plan_id: str | None = None      # evaluate projected fatigue
    at_time: datetime | None = None

class FatigueOutput(BaseModel):
    items: list[CrewFatigue]     # crew_id, now, projected_peak, risk_band (GREEN/AMBER/RED), drivers
    base_summary: dict[str, dict[str, int]]   # counts by band
```
Thresholds: GREEN < 0.4, AMBER 0.4-0.7, RED > 0.7 (RED crew excluded from scheduling).

## Uses
Writes `fatigue_score` to crew table; linear coefficients φ_i feed `schedule_crew`.

## Validation
Unit tests with canonical scenarios (24 h wake -> high fatigue; 8 h sleep resets); monotonicity checks; sensitivity analysis.

## Caveat
Prototype model for decision support demos; not a certified aeromedical tool.
