from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.aircraft_config import load_scenario
from backend.app.data.generators.aircraft import generate_fleet
from backend.app.data.simulation.aircraft import hazard_probability
from backend.app.schemas.aircraft import AircraftEvent, AircraftState, AircraftStatus, canonical_type

UTC=timezone.utc

def test_deterministic_150_aircraft_and_named_rng_streams():
    scenario=load_scenario("config/scenarios/aircraft_demo.yaml",seed=42)
    one,two=generate_fleet(scenario,"fixture"),generate_fleet(scenario,"fixture")
    assert len(one.states)==150 and one.states==two.states and one.checksum()==two.checksum()
    assert {state.type for state in one.states} == {"Su-30MKI","Rafale","Tejas","Mirage-2000","Jaguar","C-130J","IL-78","AEW&C","helicopter"}

def test_canonical_types_validation_and_event_identity():
    assert canonical_type("AWACS")=="AEW&C" and canonical_type("Heli")=="helicopter"
    state=generate_fleet(load_scenario("config/scenarios/aircraft_demo.yaml"),"contract").states[0]
    event=AircraftEvent.from_state(state,kind="seed",prior_status=None)
    assert event.state()==state and event.event_id==AircraftEvent.from_state(state,kind="seed",prior_status=None).event_id
    with pytest.raises(ValidationError): AircraftState.model_validate({**state.model_dump(),"fuel_kg":float("nan")})

def test_state_rules_and_hazard_are_safe():
    state=generate_fleet(load_scenario("config/scenarios/aircraft_demo.yaml"),"rules").states[0]
    with pytest.raises(ValidationError): AircraftState.model_validate({**state.model_dump(),"status":"FMC","status_reason":"fault"})
    assert 0 < hazard_probability(100,200,.25) < hazard_probability(100,200,1)
    assert hazard_probability(200,200,1,surge=1.3)>hazard_probability(200,200,1)
