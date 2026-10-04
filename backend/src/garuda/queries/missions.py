"""Read-only mission filtering over a scenario snapshot."""

from garuda.contracts.geo import TimeWindow
from garuda.contracts.tool import ToolInputError
from garuda.schemas.mission import Mission


def get_mission_requests(
    missions: list[Mission],
    *,
    status: list[str] | None = None,
    types: list[str] | None = None,
    priority_max: int | None = None,
    window: TimeWindow | None = None,
) -> list[Mission]:
    """Match all supplied filters; touching window endpoints do not overlap."""
    statuses = None if status is None else set(status)
    mission_types = None if types is None else set(types)
    matched = [
        mission
        for mission in missions
        if (statuses is None or mission.status in statuses)
        and (mission_types is None or mission.type in mission_types)
        and (priority_max is None or mission.priority <= priority_max)
    ]
    if window is not None:
        try:
            matched = [
                mission
                for mission in matched
                if mission.window_start < window.end and mission.window_end > window.start
            ]
        except TypeError as exc:
            raise ToolInputError(
                "window timestamps must have the same timezone awareness as mission windows; "
                "use an explicit UTC offset for timezone-aware mission records"
            ) from exc
    # The ontology's missions_for_target query uses the same ordering.
    matched.sort(key=lambda mission: (mission.priority, mission.mission_id))
    return matched
