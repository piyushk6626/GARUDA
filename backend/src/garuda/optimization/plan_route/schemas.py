"""Input/output models from docs/03_optimization_layer/threat_aware_route_planning.md."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from garuda.contracts.geo import LatLon, Waypoint


class RouteWeights(BaseModel):
    threat: float = 5.0
    weather: float = 2.0
    civil: float = 0.5
    terrain: float = 1.0


class RouteSummary(BaseModel):
    label: str
    distance_km: float
    cumulative_risk: float


class PlanRouteInput(BaseModel):
    origin: LatLon
    destination: LatLon
    aircraft_type: str
    depart_time: datetime
    altitude_profile: Literal["LOW", "MEDIUM", "HIGH", "AUTO"] = "AUTO"
    weights: RouteWeights = Field(default_factory=RouteWeights)
    max_risk: float = 0.3
    algorithm: Literal["astar", "thetastar", "rrtstar"] = "thetastar"


class PlanRouteOutput(BaseModel):
    waypoints: list[Waypoint]
    distance_km: float
    fuel_kg: float
    cumulative_risk: float
    airspace_crossed: list[str]
    alternatives: list[RouteSummary]
