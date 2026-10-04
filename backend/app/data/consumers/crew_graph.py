"""Run-scoped graph projection; shared AircraftType nodes are preserved."""
from __future__ import annotations
from typing import Any
from ...schemas.crew import CrewEvent
CONSTRAINTS=("CREATE CONSTRAINT crew_identity IF NOT EXISTS FOR (c:Crew) REQUIRE c.crew_id IS UNIQUE","CREATE CONSTRAINT crew_type_identity IF NOT EXISTS FOR (a:AircraftType) REQUIRE a.type IS UNIQUE","CREATE CONSTRAINT crew_squadron_identity IF NOT EXISTS FOR (s:CrewSquadron) REQUIRE s.key IS UNIQUE","CREATE CONSTRAINT crew_base_identity IF NOT EXISTS FOR (b:CrewBase) REQUIRE b.key IS UNIQUE")
class CrewGraphProjection:
    def __init__(self,driver:Any)->None:self.driver=driver
    async def bootstrap(self)->None:
        async with self.driver.session() as s:
            for query in CONSTRAINTS:await s.run(query)
    async def apply(self,event:CrewEvent)->bool:
        state=event.state(); qualifications=[{"type":kind,"values":list(values)} for kind,values in state.qualifications.items()]
        query="""MERGE (c:Crew {crew_id:$id}) WITH c WHERE coalesce(c.version,0)<$version
        SET c.run_id=$run,c.version=$version,c.callsign=$callsign,c.role=$role,c.base_id=$base,c.sqn_id=$sqn,c.updated_at=$updated
        MERGE (s:CrewSquadron {key:$run+':'+$sqn}) MERGE (b:CrewBase {key:$run+':'+$base})
        WITH c,s,b OPTIONAL MATCH (c)-[x:MEMBER_OF|LOCATED_AT|QUALIFIED_ON]->() DELETE x
        WITH c,s,b MERGE (c)-[:MEMBER_OF]->(s) MERGE (c)-[:LOCATED_AT]->(b)
        WITH c UNWIND $qualifications AS q MERGE (t:AircraftType {type:q.type}) MERGE (c)-[r:QUALIFIED_ON]->(t) SET r.qualifications=q.values RETURN c.version AS version"""
        async with self.driver.session() as s:
            result=await s.run(query,id=state.crew_id,run=state.run_id,version=state.version,callsign=state.callsign,role=state.role.value,base=state.base_id,sqn=state.sqn_id,updated=state.updated_at.isoformat(),qualifications=qualifications)
            return await result.single() is not None
class CrewGraphConsumer:
    def __init__(self,consumer:Any,projection:CrewGraphProjection)->None:self.consumer,self.projection=consumer,projection
    async def process_one(self)->bool:
        message=await self.consumer.getone();await self.projection.apply(CrewEvent.model_validate_json(message.value));await self.consumer.commit();return True
