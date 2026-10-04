CREATE CONSTRAINT aircraft_tail_unique IF NOT EXISTS FOR (a:Aircraft) REQUIRE a.tail_no IS UNIQUE;
CREATE CONSTRAINT squadron_id_unique IF NOT EXISTS FOR (s:Squadron) REQUIRE s.key IS UNIQUE;
CREATE CONSTRAINT base_id_unique IF NOT EXISTS FOR (b:Base) REQUIRE b.key IS UNIQUE;
