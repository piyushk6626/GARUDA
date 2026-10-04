"""In-memory aircraft availability for the harness and assignment tools."""

from garuda.schemas.aircraft import Aircraft


def get_aircraft_availability(
    aircraft: list[Aircraft],
    *,
    base_id: str | None = None,
    type_name: str | None = None,
    status: str | None = None,
    capabilities: list[str] | None = None,
    limit: int = 200,
) -> list[Aircraft]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    required = set(capabilities or [])
    matched = [
        airframe
        for airframe in aircraft
        if (base_id is None or airframe.base_id == base_id)
        and (type_name is None or airframe.type == type_name)
        and (status is None or airframe.status == status)
        and required.issubset(airframe.capabilities)
    ]
    matched.sort(key=lambda airframe: airframe.tail_no)
    return matched[:limit]


def is_dispatch_ready(airframe: Aircraft) -> bool:
    """FMC, with enough fuel and inspection margin for dispatch."""
    if airframe.status != "FMC" or airframe.maint_return_eta is not None:
        return False
    if airframe.fuel_kg < airframe.fuel_capacity_kg * 0.2:
        return False
    return airframe.hours_to_next_inspection >= 1.0


def dispatch_candidates(
    aircraft: list[Aircraft],
    *,
    base_id: str | None = None,
    type_name: str | None = None,
    capabilities: list[str] | None = None,
    limit: int = 200,
) -> list[Aircraft]:
    matched = get_aircraft_availability(
        aircraft,
        base_id=base_id,
        type_name=type_name,
        status="FMC",
        capabilities=capabilities,
        limit=limit,
    )
    return [airframe for airframe in matched if is_dispatch_ready(airframe)]
