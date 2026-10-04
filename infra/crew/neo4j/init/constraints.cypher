CREATE CONSTRAINT crew_identity IF NOT EXISTS FOR (c:Crew) REQUIRE c.crew_id IS UNIQUE;
CREATE CONSTRAINT crew_type_identity IF NOT EXISTS FOR (a:AircraftType) REQUIRE a.type IS UNIQUE;
CREATE CONSTRAINT crew_squadron_identity IF NOT EXISTS FOR (s:CrewSquadron) REQUIRE s.key IS UNIQUE;
CREATE CONSTRAINT crew_base_identity IF NOT EXISTS FOR (b:CrewBase) REQUIRE b.key IS UNIQUE;
