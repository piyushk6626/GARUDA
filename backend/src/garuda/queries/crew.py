"""Crew eligibility at an explicit time. Stored state is not an available flag."""

from datetime import datetime, timedelta

from garuda.schemas.crew import DUTY_LIMIT_HOURS, REST_MINIMUM_HOURS, Crew

QUALIFICATION_WINDOW = timedelta(days=90)
NIGHT_WINDOW = timedelta(days=90)
AAR_WINDOW = timedelta(days=180)


def availability_blockers(
    member: Crew,
    *,
    at: datetime,
    aircraft_type: str | None = None,
    need_night: bool = False,
    need_aar: bool = False,
) -> list[str]:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    reasons: list[str] = []
    if member.medical_status != "FIT":
        reasons.append(f"medical_{member.medical_status.lower()}")
    if member.leave_until is not None and member.leave_until > at:
        reasons.append("on_leave")
    if member.current_assignment is not None:
        reasons.append("assigned")
    if member.duty_hours_24h > DUTY_LIMIT_HOURS:
        reasons.append("duty_limit")
    if member.duty_hours_24h == 0 and member.rest_hours_since_last_duty < REST_MINIMUM_HOURS:
        reasons.append("rest_minimum")
    if member.duty_hours_24h > 0:
        reasons.append("on_duty")
    if member.fatigue_score > 0.7:
        reasons.append("fatigue_red")
    if aircraft_type is not None and aircraft_type not in member.qualifications:
        reasons.append("unqualified")
    elif aircraft_type is not None and not _current(member.currency.by_type.get(aircraft_type), at, QUALIFICATION_WINDOW):
        reasons.append("qualification_currency")
    if need_night and not _current(member.currency.night, at, NIGHT_WINDOW):
        reasons.append("night_currency")
    if need_aar and not _current(member.currency.aar, at, AAR_WINDOW):
        reasons.append("aar_currency")
    return reasons


def get_available_crew(
    crew: list[Crew],
    *,
    at: datetime,
    aircraft_type: str | None = None,
    role: str | None = None,
    base_id: str | None = None,
    need_night: bool = False,
    need_aar: bool = False,
    limit: int = 200,
) -> list[Crew]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    available = [
        member
        for member in crew
        if (role is None or member.role == role)
        and (base_id is None or member.base_id == base_id)
        and not availability_blockers(
            member,
            at=at,
            aircraft_type=aircraft_type,
            need_night=need_night,
            need_aar=need_aar,
        )
    ]
    available.sort(key=lambda member: member.crew_id)
    return available[:limit]


def _current(moment: datetime | None, at: datetime, window: timedelta) -> bool:
    if moment is None:
        return False
    return at - window <= moment <= at
