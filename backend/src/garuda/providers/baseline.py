"""Rule-based provider defaults (no ML, no solver). TODO(phase1): one class per protocol.

Planned baselines:
- AvailabilityProvider: status + maint ETA via queries.aircraft.is_dispatch_ready
- ComponentRiskProvider: from hours_to_next_inspection
- WeatherGoProvider: flyability threshold on queries.weather.get_weather_along_route
- RouteProvider: great-circle sampled against risk raster
- CapacityProvider: FMC count * 60 / turnaround_min
- PkProvider: WeaponType.pk_by_target_class adjusted by hardness
- FatigueProvider: Crew.fatigue_score + constant per-slot increment
- ThreatRiskProvider: raster from scenario threats
"""


def baseline_providers():
    raise NotImplementedError
