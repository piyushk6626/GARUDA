# Threat-Aware Route Planning

Tool: `plan_route`

## Problem
Find a route from base to target (and back) minimising a weighted sum of distance/fuel, threat exposure, weather risk, and civil-traffic/airspace penalties, within range and altitude limits.

## Cost surface
Grid (e.g. 2 km, optional altitude layers LOW/MED/HIGH). For cell c at time t and altitude layer h:
`cost(c) = d(c) · [1 + α · risk_threat(c,h) + β · (1 − flyability(c,t)) + γ · civil_density(c) + δ · terrain_penalty(c,h)]`
Blocked: prohibited/closed airspace, outside altitude bounds, risk ≥ r_max.
`risk_threat = 1 − Π(1 − pk_i · conf_i · 1[in envelope])` (see `threats.md`).

## Algorithms
1. **A\*** on 8-connected grid with heuristic = great-circle distance x min cost factor (admissible). Time-dependent cost via snapshot buckets of weather/threat.
2. **Theta\*** (any-angle) to reduce zig-zag paths using line-of-sight cost integration.
3. **RRT\*** alternative for 3-D with kinematic limits (turn radius), asymptotically optimal; use if grid resolution is limiting.
4. Post-process: smooth, add waypoints, compute fuel/time, check range incl. AAR.

## Pseudocode (A*)
```
open = PQ{(start,0)}; g[start]=0
while open:
  n = pop min f=g+h
  if n==goal: return reconstruct
  for m in neighbors(n):
     if blocked(m): continue
     g2 = g[n] + step_cost(n,m,t(g[n]))
     if g2 < g[m]: g[m]=g2; parent[m]=n; push(m, g2+h(m))
```
Round-trip: plan ingress and egress with different cost layers (egress treats SEAD-suppressed threats as lower risk).

## Tool signature
```python
class PlanRouteInput(BaseModel):
    origin: LatLon
    destination: LatLon
    aircraft_type: str
    depart_time: datetime
    altitude_profile: Literal["LOW","MEDIUM","HIGH","AUTO"] = "AUTO"
    weights: RouteWeights = RouteWeights(threat=5.0, weather=2.0, civil=0.5, terrain=1.0)
    max_risk: float = 0.3
    algorithm: Literal["astar","thetastar","rrtstar"] = "thetastar"

class PlanRouteOutput(BaseModel):
    waypoints: list[Waypoint]     # lat, lon, alt_ft, eta
    distance_km: float
    fuel_kg: float
    cumulative_risk: float
    airspace_crossed: list[str]
    alternatives: list[RouteSummary]   # e.g. shortest vs safest
```

## Libraries
NumPy, `heapq`/`scikit-image` `route_through_array` for baseline, `networkx` optional, own Theta*; `rasterio`, `shapely`.

## Complexity
Grid 600x600 = 360k cells; A* O(N log N), <1 s with numpy cost layers; cached risk raster per threat-version.

## Explainability
Report the main detours ("diverts 40 km around SAM-07 ring"), risk contributions by threat, weather segments.

## Failure modes
No route within risk cap -> return least-risk route with warning or suggest SEAD.

## Validation
Test on synthetic grids with known optimum; check that no waypoint is inside blocked cells.
