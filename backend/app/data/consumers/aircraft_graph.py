"""Version-guarded Neo4j projection. PostgreSQL remains authoritative."""
from __future__ import annotations
from typing import Any
from ...schemas.aircraft import AircraftEvent

CONSTRAINTS = (
    "CREATE CONSTRAINT aircraft_tail_unique IF NOT EXISTS FOR (a:Aircraft) REQUIRE a.tail_no IS UNIQUE",
    "CREATE CONSTRAINT squadron_id_unique IF NOT EXISTS FOR (s:Squadron) REQUIRE s.key IS UNIQUE",
    "CREATE CONSTRAINT base_id_unique IF NOT EXISTS FOR (b:Base) REQUIRE b.key IS UNIQUE",
)

class AircraftGraphProjection:
    def __init__(self, driver: Any) -> None: self.driver=driver
    async def bootstrap(self) -> None:
        async with self.driver.session() as session:
            for query in CONSTRAINTS: await session.run(query)
    async def apply(self,event: AircraftEvent)->bool:
        s=event.state()
        query="""
        MERGE (a:Aircraft {tail_no:$tail})
        WITH a WHERE coalesce(a.version,0) < $version
        SET a.run_id=$run,a.version=$version,a.status=$status,a.base_id=$base,a.updated_at=$updated
        MERGE (sq:Squadron {key:$run + ':' + $sqn}) SET sq.run_id=$run,sq.sqn_id=$sqn
        MERGE (home:Base {key:$run + ':home:' + $home}) SET home.run_id=$run,home.base_id=$home
        MERGE (current:Base {key:$run + ':current:' + $base}) SET current.run_id=$run,current.base_id=$base
        MERGE (sq)-[:BASED_AT]->(home)
        WITH a,sq,current OPTIONAL MATCH (a)-[old:ASSIGNED_TO]->() DELETE old
        WITH a,sq,current MERGE (a)-[:ASSIGNED_TO]->(sq)
        WITH a,current OPTIONAL MATCH (a)-[previous:LOCATED_AT]->() DELETE previous
        WITH a,current MERGE (a)-[:LOCATED_AT]->(current) RETURN a.version AS version
        """
        async with self.driver.session() as session:
            result=await session.run(query,tail=s.tail_no,run=s.run_id,version=s.version,status=s.status.value,base=s.base_id,sqn=s.sqn_id,home=s.base_id,updated=s.updated_at.isoformat())
            return await result.single() is not None

class AircraftGraphConsumer:
    def __init__(self,consumer:Any,projection:AircraftGraphProjection)->None:self.consumer,self.projection=consumer,projection
    async def process_one(self)->bool:
        message=await self.consumer.getone(); await self.projection.apply(AircraftEvent.model_validate_json(message.value)); await self.consumer.commit(); return True
