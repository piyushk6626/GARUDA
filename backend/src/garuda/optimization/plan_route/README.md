# plan_route

Implements the existing `async plan_route(PlanRouteInput, ToolContext) ->
PlanRouteOutput` contract. Schemas, defaults, ToolSpec, provider protocols, and
shared contracts are unchanged. Reads a single scenario snapshot and existing
terrain files; writes no plans, scenario data, or files.

The output describes **one leg from origin to destination**. This matches the
existing ToolSpec and RouteProvider. A caller needing ingress and egress makes
two requests, with the appropriate departure times and updated threat snapshot.

## Routing and constraints

- Quick, validated straight/dogleg routes supply feasible incumbents. A* uses
  eight grid neighbours. Theta* also checks shortcuts from parents,
  integrating the full edge cost. RRT* uses a feasible grid warm start, seeded
  sampling, near-parent selection, and rewiring with descendant revalidation.
- LOW/MEDIUM/HIGH correspond to 3,000/18,000/35,000 feet. AUTO evaluates the
  available fixed bands below the aircraft ceiling and selects the lowest cost.
  No climb, bank-angle, or turn-radius parameters exist in the public contract;
  this implementation makes no aircraft kinematics guarantee.
- Route costs use the documented distance-weighted threat/weather/civil/terrain
  terms. Geodesic threat rings are conservatively circumscribed by a 360-vertex
  polygon with a one-metre buffer. Whole-segment intersections prevent tunnelling
  through narrow threat envelopes or airspace zones.
  Geographic airspace edges are densified before projection, with a one-metre
  buffer to cover interpolation error.
- Threat positions use the latest mobile sighting available at departure.
  Confidence decays with the existing 12/72-hour mobile/fixed time constants.
  Future sightings and not-yet-observed threats are excluded; destroyed sites
  contribute no risk. Suppressed sites retain their scenario's reduced pk_base.
  Threat positions and probabilities are held fixed for the leg; no unprovided
  future motion or SEAD effect is invented.
- `max_risk` applies to **local combined exposure**, as the planning document's
  blocked-cell rule specifies. Positive exposure at or above the cap is blocked;
  zero exposure is permitted when the cap is zero. Cumulative risk is
  `1 - product(1 - pk * confidence)` over distinct encountered threats. It does
  not increase merely because more waypoints sample the same threat. Separate
  low-risk envelopes can consequently produce cumulative risk above max_risk.
- Prohibited zones and zones with capacity_per_hour=0 are always blocked while
  active at the flown altitude. Activation windows are checked at actual crossing
  times and have exclusive end times. Other active zones add civil penalties and
  appear in airspace_crossed. This is not capacity-slot deconfliction.
- Weather uses the nearest forecast cell within 45 km and forecast timestamp
  within one hour, issued by departure. Flyability includes the existing icing
  rule. No forecast coverage makes an edge unavailable; an entirely empty
  weather collection contributes neutral weather cost. Civil cost uses the
  latest airborne ADS-B record per aircraft from the preceding 15 minutes,
  with a 5 km density kernel and 2,000-foot vertical relevance band.
- Terrain reads the project's `processed/dem_1km.npy` and optional terrain
  penalty array beneath `gis_root()` (`GARUDA_GIS_DIR` override). Shapes and
  values must match snapshot metadata. When the DEM is absent, the snapshot's
  maximum elevation is used conservatively across its extent. The clearance
  margin is 1,000 feet; unknown terrain outside the extent is blocked. Adjacent
  raster cells are checked conservatively to avoid missing a ridge at a corner.
- Flight time uses cruise_kts; fuel is distance/range_km * fuel_capacity_kg.
  Distance and ETAs use WGS84 geodesic legs. The aircraft type's range constrains
  the whole leg. No AAR or aircraft-specific starting fuel is assumed.

## Bounded search and failure behaviour

Search uses the range-feasible ellipse's bounding square, a nominal 2 km mesh
adapted to approximately 40,000 grid nodes, and five-minute arrival-time labels
with Pareto cost/distance pruning. Searches share a 16-second budget, reserving
time for other altitude bands, fallback, and alternatives. Results are feasible
routes found within that budget, without a global-optimality guarantee. RRT*
uses at most 250 samples following its grid warm start. Returned points are
densified to at most 1 km and their complete segments are validated again.

If capped routing finds no route, the requested fallback minimizes cumulative
distinct-threat risk, then distance, while preserving all other constraints.
When the returned route violates the local cap, the tool emits a RuntimeWarning
and includes `least-risk (risk cap exceeded)` in alternatives. This preserves the
frozen output fields. `shortest` and `safest` alternatives are searched at the
selected altitude; they may be identical, and a search finding no candidate
does not add an alternative. A failed bounded search raises the existing
ToolInputError and does not claim mathematically proven infeasibility.

CPU work uses asyncio.to_thread because the shared tools.executor.run_cpu is
still a stub. The optional LangGraph adapter invokes the real tool with payload
and ctx. Shared registry/provider/envelope wiring remains owned by the project.

Run tests from backend:

```powershell
python -m pytest tests/test_plan_route.py -q --basetemp=.pytest_cache/plan-route-tests
```
