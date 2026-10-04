# Sortie capacity tool

`compute_sortie_capacity(payload, ctx)` solves one continuous PuLP LP per selected
base. The existing input/output schemas, `ToolContext`, providers and `SPEC` are
unchanged. Install the existing backend `opt` extra for PuLP and HiGHS or CBC.
Preparation and solving run in a worker thread while the shared CPU executor is
still a stub. No scenario or plan is modified.

The scenario lacks several LP parameters. The following prototype assumptions
were approved for this implementation:

- Initial fuel stock equals `fuel_capacity_l * 0.8` kg; no resupply is assumed.
  Each sortie consumes one full aircraft fuel capacity, including its initial
  sortie. Aircraft onboard fuel is not added to the base stock.
- One ground team works for the aircraft's turnaround duration per sortie.
  Each team supplies one team-hour per hour. Surge raises these hours by 15%
  and reduces turnaround times by 15%.
- All sorties have equal objective weight. Demand-driven mode is not exposed
  by the current input contract.
- Each aircraft type's load configurations are alternative consumption choices.
  Station quantities are summed, reservations are deducted from weapon stocks,
  and missing stock means zero. Pods are reusable equipment and are not consumed.
  Types with no load configuration can generate unarmed support sorties.
- `max_sorties_per_hour` is already a sortie limit: the runway operations limit
  is twice that number. It is not halved or multiplied by runway count again.
- Shelters limit simultaneous turnaround occupancy and are reported under the
  `aircraft` constraint family.

Availability is the sum of `AvailabilityProvider.p_fmc` across aircraft in a group
at each requested hour. Groups distinguish type, turnaround and fuel capacity.
`WeatherGoProvider.p_go` is queried at the base for each hour with guidance `NONE`;
probability below 0.5 closes the base. These forecasts must return finite values
in [0, 1]. The tool does not call `CapacityProvider`, avoiding circular providers.

The hourly aircraft window uses fractional overlap for noninteger turnaround
durations: coefficient `min(1, max(0, turnaround_hours - (p-q)))` for hour `q`
in hour `p`'s trailing window. Integer durations recover the documented sliding
sum; subhour durations allow multiple turnarounds in an hour. This is an hourly
continuous approximation, not an exact individual aircraft schedule.

Crew must pass the existing roster eligibility query and a forecast fatigue
threshold of 0.7. Fractional individual seat allocations enforce aircraft type
qualifications, role requirements, and prevent cross-qualified crew from being
counted twice. The prototype uses one pilot seat by default, pilot + WSO for
Su-30MKI, pilot + loadmaster for C-130J, pilot + operator for AEW&C, and two pilots
for IL-78. Crew occupancy/duty uses `max(1 hour, turnaround)` per sortie, since
this request has no mission durations. Each member has at most `14 - current
duty_hours_24h` duty hours across the requested horizon. Rest cycles, mission
flight time and future roster state transitions are not inferred; detailed crew
scheduling remains a separate tool.

Hourly aggregate LP capacity is rounded down (integer noise within 1e-7 is
snapped). `totals` sums the returned integers. Rounding loss is logged at INFO
without adding an output field. These numbers are aggregate continuous capacity,
not a guarantee of an integer aircraft/load/crew assignment.

Each shadow price is the maximum absolute constraint dual within its family,
including cumulative fuel/munition constraints. Prices retain their resource
units, so compare them with care. The bottleneck is the family with the largest
price, with deterministic ties in the order aircraft, ground_crew, fuel,
munitions, runway, crew. An entirely closed horizon reports `weather`.
Only optimal, feasible solves with finite duals are returned; errors never
substitute the old stub output. The internal solver budget is eight seconds
across bases; reducing the selected bases or horizon resolves timeout errors.

The legacy `STUB_OUTPUT` constant remains solely for the existing unwired graph
skeleton, whose adapter is outside this tool implementation's scope.

Run `python -m pytest tests/test_compute_sortie_capacity.py` from `backend`.
The optional SimPy comparison checks a one-base, integer-turnaround case.
