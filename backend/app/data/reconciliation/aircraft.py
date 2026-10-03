from __future__ import annotations
from typing import Any
from ..consumers.aircraft_graph import AircraftGraphProjection
from ..consumers.aircraft_live import AircraftLiveProjection
from ...schemas.aircraft import AircraftEvent, AircraftState

class AircraftReconciler:
    """Replays a consistent SQL state snapshot through the normal version guards."""
    def __init__(self,pool:Any,live:AircraftLiveProjection|None=None,graph:AircraftGraphProjection|None=None)->None:self.pool,self.live,self.graph=pool,live,graph
    async def rebuild(self,run_id:str)->int:
        rows=await self.pool.fetch("SELECT * FROM aircraft.aircraft WHERE run_id=$1 ORDER BY tail_no",run_id); count=0
        for row in rows:
            data=dict(row); data.pop("recorded_at",None); data["capabilities"]=tuple(row["capabilities"]); state=AircraftState.model_validate(data)
            event=AircraftEvent.from_state(state,kind="reconciliation",prior_status=None)
            if self.live: await self.live.apply(event)
            if self.graph: await self.graph.apply(event)
            count+=1
        return count
