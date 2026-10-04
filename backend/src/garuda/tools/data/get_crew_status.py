"""Data tool `get_crew_status`. Backing: queries/crew.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime, timezone

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.queries.crew import availability_blockers


class CrewStatusItem(BaseModel):
    crew_id: str
    role: str
    base_id: str
    qualification: str
    fatigue_score: float
    available: bool


class GetCrewStatusInput(BaseModel):
    base_ids: list[str] | None = None
    role: str | None = None
    qualification: str | None = None
    max_fatigue: float | None = None
    available_from: datetime | None = None
    available_to: datetime | None = None


class GetCrewStatusOutput(BaseModel):
    total: int
    items: list[CrewStatusItem]


SPEC = ToolSpec(
    name="get_crew_status",
    description="Crew by qualification, fatigue, and availability. Use before scheduling; not for a fatigue forecast.",
    input_model=GetCrewStatusInput,
    output_model=GetCrewStatusOutput,
    side_effects="none",
    timeout_s=10,
)

# Compatibility fixture for the separate data graph scaffold.
# The callable tool below reads the scenario instead.
STUB_OUTPUT = GetCrewStatusOutput(
    total=1,
    items=[
        CrewStatusItem(
            crew_id="C-001",
            role="PILOT",
            base_id="B1",
            qualification="Su-30MKI",
            fatigue_score=0.3,
            available=True,
        )
    ],
)


async def get_crew_status(payload: GetCrewStatusInput, ctx: ToolContext) -> GetCrewStatusOutput:
    """Read stored crew status, with one row per crew ordered by crew_id.

    Qualification matches an exact aircraft-type key. Return that type when
    filtered, otherwise the primary type, and check currency for the same type.
    Fatigue is the stored score, not a forecast. Without an explicit availability
    window, retain unavailable crew and report eligibility at scenario_time.
    An explicit window filters for availability throughout the closed interval;
    a single endpoint is a point-in-time check. Equal endpoints are allowed.
    Stored duty, rest, fatigue, medical status, and assignments remain fixed.
    """
    has_window = payload.available_from is not None or payload.available_to is not None
    for name, moment in (
        ("available_from", payload.available_from),
        ("available_to", payload.available_to),
        ("scenario_time", None if has_window else ctx.scenario_time),
    ):
        if moment is not None and (moment.tzinfo is None or moment.utcoffset() is None):
            raise ToolInputError(f"{name} must be timezone-aware")
    start = (
        payload.available_from if payload.available_from is not None
        else payload.available_to if payload.available_to is not None
        else ctx.scenario_time
    ).astimezone(timezone.utc)
    end = start if payload.available_to is None else payload.available_to.astimezone(timezone.utc)
    if end < start:
        raise ToolInputError("available_to must be at or after available_from")
    base_ids = None if payload.base_ids is None else set(payload.base_ids)
    if base_ids is not None:
        known_bases = set(ctx.scenario.base_ids())
        unknown = base_ids - known_bases
        if unknown:
            raise ToolInputError(
                f"unknown base_id {', '.join(sorted(unknown))}; "
                f"valid: {', '.join(sorted(known_bases))}"
            )
    items: list[CrewStatusItem] = []
    for member in ctx.scenario.snapshot().crew:
        if (
            (base_ids is not None and member.base_id not in base_ids)
            or (payload.role is not None and member.role != payload.role)
            or (payload.qualification is not None and payload.qualification not in member.qualifications)
            or (payload.max_fatigue is not None and member.fatigue_score > payload.max_fatigue)
        ):
            continue
        qualification = member.primary_type if payload.qualification is None else payload.qualification
        # Leave availability is monotone and currency is one contiguous interval.
        # With the other stored blockers fixed, both endpoints suffice for the
        # entire closed window, including expiry exactly at its end.
        available = not availability_blockers(member, at=start, aircraft_type=qualification)
        if available and end != start:
            available = not availability_blockers(member, at=end, aircraft_type=qualification)
        if has_window and not available:
            continue
        items.append(
            CrewStatusItem(
                crew_id=member.crew_id,
                role=member.role,
                base_id=member.base_id,
                qualification=qualification,
                fatigue_score=member.fatigue_score,
                available=available,
            )
        )
    items.sort(key=lambda item: item.crew_id)
    return GetCrewStatusOutput(total=len(items), items=items)
