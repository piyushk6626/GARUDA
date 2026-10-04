"""Frozen provider interfaces. These decouple tools from each other."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np

from garuda.contracts.geo import LatLon, TimeWindow
from garuda.contracts.plan import SortieSlot


class AvailabilityProvider(Protocol):
    def p_fmc(self, tail_no: str, at: datetime) -> float: ...


class ComponentRiskProvider(Protocol):
    def aircraft_risk(self, tail_no: str, hours: float) -> float: ...


class WeatherGoProvider(Protocol):
    def p_go(self, launch_base: str, route: list[LatLon], window: TimeWindow, guidance: str) -> float: ...


class RouteProvider(Protocol):
    def route(self, origin: LatLon, dest: LatLon, aircraft_type: str, depart: datetime) -> list[LatLon]: ...


class CapacityProvider(Protocol):
    def cap(self, base_id: str, hour: datetime) -> int: ...


class PkProvider(Protocol):
    def pk(self, config_id: str, mission_id: str) -> float: ...


class FatigueProvider(Protocol):
    def fatigue(self, crew_id: str, at: datetime) -> float: ...
    def phi(self, slot: SortieSlot) -> float: ...


class ThreatRiskProvider(Protocol):
    def raster(self, at: datetime, altitude_band: str) -> np.ndarray: ...


@dataclass(frozen=True)
class Providers:
    availability: AvailabilityProvider
    component_risk: ComponentRiskProvider
    weather_go: WeatherGoProvider
    route: RouteProvider
    capacity: CapacityProvider
    pk: PkProvider
    fatigue: FatigueProvider
    threat_risk: ThreatRiskProvider
