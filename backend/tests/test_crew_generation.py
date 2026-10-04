from pathlib import Path

import pytest
from pydantic import ValidationError

from garuda.config import load_config
from garuda.generators.crew import fatigue_band, fatigue_score
from garuda.generators.missions import build_scenario
from garuda.queries.crew import availability_blockers, get_available_crew
from garuda.schemas.crew import Crew

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
AAR_TYPES = {"Su-30MKI", "Rafale", "IL-78"}


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_roster_counts_currency_and_stable_ids() -> None:
    first = _scenario()
    other = _scenario(9)
    crew = first.crew
    assert len(crew) == 300
    assert len(first.crew_duty) == 300
    assert sum(member.role == "PILOT" for member in crew) == 248
    assert sum(member.role == "WSO" for member in crew) == 40
    assert sum(member.role == "LOADMASTER" for member in crew) == 8
    assert sum(member.role == "AEW_OP" for member in crew) == 4
    assert sum(member.medical_status == "FIT" for member in crew) == 279
    assert sum(member.medical_status == "LIMITED" for member in crew) == 15
    assert sum(member.medical_status == "GROUNDED" for member in crew) == 6
    assert sum(member.leave_until is not None for member in crew) == 15
    assert sum(len(member.qualifications) > 1 for member in crew) == 60
    assert sum(member.currency.night is not None for member in crew) == 210
    aar = [member for member in crew if member.primary_type in AAR_TYPES]
    assert len(aar) == 140
    assert sum(member.currency.aar is not None for member in aar) == 84
    assert [member.crew_id for member in crew] == [member.crew_id for member in other.crew]
    assert [member.callsign for member in crew] == [f"CS-{index:04d}" for index in range(1, 301)]
    assert crew[0].flight_hours_total != other.crew[0].flight_hours_total


def test_flight_leads_are_the_most_experienced_pilots() -> None:
    crew = _scenario().crew
    leads = 0
    for primary in {member.primary_type for member in crew}:
        pilots = [member for member in crew if member.role == "PILOT" and member.primary_type == primary]
        ranked = sorted(pilots, key=lambda member: (-member.flight_hours_total, member.crew_id))
        expected = {member.crew_id for member in ranked[: round(len(ranked) * 0.25)]}
        actual = {member.crew_id for member in pilots if member.is_flight_lead}
        assert actual == expected
        leads += len(actual)
    assert leads == 62
    assert not any(member.is_flight_lead and member.role != "PILOT" for member in crew)


def test_fatigue_band_and_duty_direction() -> None:
    start = _scenario().aircraft[0].updated_at
    rested = fatigue_score(0, 18, start)
    working = fatigue_score(10, 0, start)
    assert 0 <= rested <= 1
    assert 0 <= working <= 1
    assert working >= rested
    assert fatigue_band(0.7) == "AMBER"
    assert fatigue_band(0.7001) == "RED"
    assert fatigue_band(0.2) == "GREEN"


def test_eligibility_boundaries() -> None:
    scenario = _scenario()
    start = scenario.crew[0].updated_at
    grounded = next(member for member in scenario.crew if member.medical_status == "GROUNDED")
    assert "medical_grounded" in availability_blockers(grounded, at=start)
    fit = next(
        member
        for member in scenario.crew
        if member.medical_status == "FIT"
        and member.leave_until is None
        and member.duty_hours_24h == 0
        and member.role == "PILOT"
    )
    available = get_available_crew(scenario.crew, at=start, role="PILOT", aircraft_type=fit.primary_type)
    assert available
    assert all(member.medical_status == "FIT" and member.duty_hours_24h == 0 for member in available)
    red = fit.model_copy(update={"fatigue_score": 0.71})
    amber = fit.model_copy(update={"fatigue_score": 0.7})
    assert "fatigue_red" in availability_blockers(red, at=start, aircraft_type=fit.primary_type)
    assert "fatigue_red" not in availability_blockers(amber, at=start, aircraft_type=fit.primary_type)
    overloaded = fit.model_copy(update={"duty_hours_24h": 15, "rest_hours_since_last_duty": 0})
    assert "duty_limit" in availability_blockers(overloaded, at=start)
    with pytest.raises(ValidationError):
        Crew.model_validate(
            {**fit.model_dump(), "duty_hours_24h": 0, "rest_hours_since_last_duty": 11.9}
        )
