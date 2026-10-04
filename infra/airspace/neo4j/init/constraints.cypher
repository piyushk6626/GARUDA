CREATE CONSTRAINT airspace_zone_unique IF NOT EXISTS FOR (z:AirspaceZone) REQUIRE z.zone_id IS UNIQUE;
CREATE CONSTRAINT airspace_mission_unique IF NOT EXISTS FOR (m:Mission) REQUIRE m.key IS UNIQUE;
