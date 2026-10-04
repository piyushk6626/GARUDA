"""Public airfields for the western theatre.

Positions, elevation, and runway true heading come from OurAirports.
Shelter, fuel, and sortie capacity stay on the GIS `base` table.
"""

from typing import NamedTuple


class Airfield(NamedTuple):
    base_id: str
    name: str
    icao: str
    lat: float
    lon: float
    elevation_ft: int
    runway_true_deg: float


BASES: tuple[Airfield, ...] = (
    Airfield("B1", "Jodhpur", "VIJO", 26.2511, 73.0489, 717, 50),
    Airfield("B2", "Jaisalmer", "VIJR", 26.8887, 70.8650, 751, 40),
    Airfield("B3", "Bikaner (Nal)", "VIBK", 28.0706, 73.2072, 752, 50),
    Airfield("B4", "Ambala", "VIAM", 30.3681, 76.8167, 900, 122),
    Airfield("B5", "Adampur", "VIAX", 31.4338, 75.7588, 775, 130),
    Airfield("B6", "Bhuj", "VABJ", 23.2878, 69.6702, 268, 50),
    Airfield("B7", "Agra", "VIAG", 27.1578, 77.9609, 551, 50),
)


def base_by_id(base_id: str) -> Airfield:
    for airfield in BASES:
        if airfield.base_id == base_id:
            return airfield
    known = ", ".join(airfield.base_id for airfield in BASES)
    raise KeyError(f"unknown base_id {base_id}; valid: {known}")


def base_by_icao(icao: str) -> Airfield | None:
    for airfield in BASES:
        if airfield.icao == icao:
            return airfield
    return None
