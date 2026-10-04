"""Neo4j projection only owns airspace nodes and their mission links."""
from __future__ import annotations
from typing import Any
from ...schemas.airspace import AirspaceEvent
CONSTRAINTS=("CREATE CONSTRAINT airspace_zone_unique IF NOT EXISTS FOR (z:AirspaceZone) REQUIRE z.zone_id IS UNIQUE","CREATE CONSTRAINT airspace_mission_unique IF NOT EXISTS FOR (m:Mission) REQUIRE m.key IS UNIQUE")
class AirspaceGraphProjection:
    def __init__(self,driver:Any)->None:self.driver=driver
    async def bootstrap(self)->None:
        async with self.driver.session() as session:
            for query in CONSTRAINTS: await session.run(query)
    async def apply(self,event:AirspaceEvent)->bool:
        z=event.zone(); links=z.provenance.get("mission_ids",[])
        query="""MERGE(z:AirspaceZone {zone_id:$id}) WITH z WHERE coalesce(z.version,0)<=$v SET z.run_id=$run,z.version=$v,z.name=$name,z.type=$type,z.active=$active
        WITH z OPTIONAL MATCH(z)<-[old:USES_AIRSPACE]-() DELETE old WITH z
        FOREACH (_ IN CASE WHEN size($missions)=0 THEN [1] ELSE [] END | DETACH DELETE z)
        WITH z WHERE size($missions)>0 UNWIND $missions AS mission MERGE(m:Mission {key:$run+':'+mission}) ON CREATE SET m.reference_stub=true,m.run_id=$run,m.mission_id=mission MERGE(m)-[:USES_AIRSPACE]->(z) RETURN z.zone_id"""
        async with self.driver.session() as session:
            result=await session.run(query,id=z.zone_id,run=z.run_id,v=z.version,name=z.name,type=z.type.value,active=event.payload["active"],missions=links)
            return await result.single() is not None
class AirspaceGraphConsumer:
    def __init__(self,consumer:Any,projection:AirspaceGraphProjection,dead_producer:Any|None=None)->None:self.consumer,self.projection,self.dead_producer=consumer,projection,dead_producer
    async def process_one(self)->bool:
        msg=await self.consumer.getone()
        try: await self.projection.apply(AirspaceEvent.model_validate_json(msg.value))
        except Exception:
            if self.dead_producer: await self.dead_producer.send_and_wait("garuda.airspace.dead_letter",msg.value,key=msg.key)
            await self.consumer.commit(); return False
        await self.consumer.commit(); return True
