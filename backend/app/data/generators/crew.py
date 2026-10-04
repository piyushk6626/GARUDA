"""Pure deterministic crew roster and interval-history generation."""
from __future__ import annotations
import hashlib,json,uuid
from dataclasses import dataclass
from datetime import timedelta
import numpy as np
from ...crew_config import CrewCatalog,CrewScenario,load_catalog
from ...aircraft_config import load_scenario as load_aircraft
from .aircraft import generate_fleet
from ...schemas.crew import CREW_EVENT_NAMESPACE,CrewRole,CrewState,Currency,DutyInterval,DutyKind,MedicalStatus
from ..fatigue.crew import fatigue_score

@dataclass(frozen=True)
class CrewSeed:
    states:tuple[CrewState,...]
    intervals:tuple[DutyInterval,...]
    catalog: CrewCatalog
    def checksum(self)->str:return hashlib.sha256(json.dumps([s.model_dump(mode="json") for s in self.states],sort_keys=True,separators=(",",":")).encode()).hexdigest()
    def manifest(self)->dict[str,object]:return {"count":len(self.states),"roles":{r.value:sum(s.role is r for s in self.states) for r in CrewRole},"checksum":self.checksum(),"generator_version":"crew-demo-v1"}
def _allocate(total:int, ratios:tuple[float,...])->list[int]:
    exact=[total*x for x in ratios]; out=[int(x) for x in exact]
    for i in sorted(range(len(out)),key=lambda i:(exact[i]-out[i],-i),reverse=True)[:total-sum(out)]:out[i]+=1
    return out
def generate_crew(scenario:CrewScenario,run_id:str,*,catalog_path:str|None=None)->CrewSeed:
    catalog=load_catalog(catalog_path or scenario.catalog); aircraft=generate_fleet(load_aircraft(scenario.aircraft_scenario,seed=scenario.seed),run_id)
    placement=np.random.default_rng(np.random.SeedSequence(scenario.seed,spawn_key=(11,))); exp=np.random.default_rng(np.random.SeedSequence(scenario.seed,spawn_key=(12,))); qualrng=np.random.default_rng(np.random.SeedSequence(scenario.seed,spawn_key=(13,))); medical=np.random.default_rng(np.random.SeedSequence(scenario.seed,spawn_key=(14,)))
    jobs:list[tuple[str,CrewRole,str,str]]=[]
    by_type:dict[str,list]= {}
    for ac in aircraft.states:by_type.setdefault(ac.type,[]).append(ac)
    for kind, items in sorted(by_type.items()):
        spec=catalog.types[kind]
        sqn=items[0].sqn_id
        for ac in sorted(items,key=lambda x:x.tail_no):
            for role,count in sorted(spec.staffing.items(),key=lambda x:x[0].value):
                for _ in range(count):jobs.append((kind,role,sqn,ac.base_id))
    if len(jobs)!=scenario.total_crew:raise ValueError(f"staffing rules produce {len(jobs)}, not configured total {scenario.total_crew}")
    statuses=[MedicalStatus.FIT]*_allocate(len(jobs),(0.93,0.05,0.02))[0]+[MedicalStatus.LIMITED]*_allocate(len(jobs),(0.93,0.05,0.02))[1]+[MedicalStatus.GROUNDED]*_allocate(len(jobs),(0.93,0.05,0.02))[2]
    medical.shuffle(statuses); on_leave=set(medical.choice(len(jobs),size=_allocate(len(jobs),(.05,.95))[0],replace=False).tolist())
    states=[]; intervals=[]
    for i,(kind,role,sqn,base) in enumerate(jobs,1):
        crew_id=f"{run_id}-CR-{i:03d}"; call=f"{kind.split('-')[0].upper()}-{i:03d}"; spec=catalog.types[kind]
        total=float(exp.gamma(3,900)); recent=min(total,float(exp.uniform(5,90)))
        q={kind:tuple(spec.qualifications)}
        legal_secondary=[item for item in spec.secondary_types if role in catalog.types[item].staffing]
        if qualrng.random()<.20 and legal_secondary:
            secondary=str(qualrng.choice(legal_secondary));q[secondary]=tuple(catalog.types[secondary].qualifications)
        dates={name:scenario.start_at-timedelta(days=int(qualrng.integers(1,80 if qualrng.random()<.7 else 160))) for name in q}
        currency=Currency(qualification=dates,night=scenario.start_at-timedelta(days=int(qualrng.integers(1,80))) if spec.night_capable and qualrng.random()<.7 else None,aar=scenario.start_at-timedelta(days=int(qualrng.integers(1,170))) if spec.aar_capable and qualrng.random()<.6 else None)
        # End with rest history; every tenth record starts duty to exercise policy boundaries.
        duty=0.0 if i%10 else 10.0
        duty_start=scenario.start_at-timedelta(hours=duty)
        rest_start=duty_start-timedelta(hours=float(placement.integers(6,25)))
        rest=DutyInterval(crew_id=crew_id,run_id=run_id,start_at=rest_start,end_at=duty_start,kind=DutyKind.SLEEP if i%3==0 else DutyKind.REST)
        intervals.append(rest)
        rest_hours=0.0 if duty else (scenario.start_at-rest_start).total_seconds()/3600
        if duty:
            intervals.append(DutyInterval(crew_id=crew_id,run_id=run_id,start_at=duty_start,end_at=None,kind=DutyKind.DUTY))
        event_id=uuid.uuid5(CREW_EVENT_NAMESPACE,f"{run_id}:{crew_id}:1")
        states.append(CrewState(crew_id=crew_id,run_id=run_id,callsign=call,role=role,sqn_id=sqn,base_id=base,primary_type=kind,qualifications=q,currency=currency,flight_hours_total=total,flight_hours_last_30d=recent,duty_hours_24h=duty,rest_hours_since_last_duty=rest_hours,fatigue_score=fatigue_score(intervals[-2:],scenario.start_at),medical_status=statuses[i-1],leave_until=scenario.start_at+timedelta(days=2) if i-1 in on_leave else None,current_assignment=None,is_flight_lead=False,logical_time=scenario.start_at,updated_at=scenario.start_at,version=1,last_event_id=event_id))
    # Leads are selected within type from pilot experience with stable crew-id tie-breaks.
    leads=set()
    for kind in sorted({s.primary_type for s in states}):
        pilots=sorted((s for s in states if s.primary_type==kind and s.role is CrewRole.PILOT),key=lambda s:(-s.flight_hours_total,s.crew_id));leads.update(s.crew_id for s in pilots[:round(len(pilots)*.25)])
    states=[s.model_copy(update={"is_flight_lead":s.crew_id in leads}) for s in states]
    return CrewSeed(tuple(states),tuple(intervals),catalog)
