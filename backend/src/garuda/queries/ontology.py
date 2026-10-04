"""Fixed Cypher templates over the Neo4j ontology. Callers pick a template; they never send Cypher.

The patterns follow what storage/neo4j_sync.py writes: crew are QUALIFIED_ON an
AircraftType node, and aircraft and crew reach a base through LOCATED_AT.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from garuda.contracts.tool import ToolInputError

if TYPE_CHECKING:
    from garuda.context.ports import GraphReader

OntologyTemplate = Literal[
    "aircraft_with_qualified_crew",
    "missions_for_target",
    "assets_at_base",
    "threats_covering_target",
    "plan_assignments",
]

ROW_LIMIT = 200

AssetKind = Literal["aircraft", "crew", "weapon_stock", "squadron"]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AircraftWithQualifiedCrewParams(_Params):
    """Aircraft at a base paired with crew at the same base who are qualified on that aircraft type."""

    base_id: str = Field(min_length=1, description="Base whose aircraft and crew to pair.")
    aircraft_type: str | None = Field(default=None, description="Only this aircraft type, e.g. Su-30MKI.")
    status: str = Field(default="FMC", min_length=1, description="Aircraft status to match.")
    max_fatigue: float | None = Field(default=None, ge=0, le=1, description="Highest crew fatigue score allowed.")


class MissionsForTargetParams(_Params):
    """Missions that target one target."""

    target_id: str = Field(min_length=1)
    status: str | None = Field(default=None, description="Only missions in this status.")


class AssetsAtBaseParams(_Params):
    """Aircraft, crew, weapon stock, and squadrons at one base."""

    base_id: str = Field(min_length=1)
    asset_kind: AssetKind | None = Field(default=None, description="Only this kind of asset.")


class ThreatsCoveringTargetParams(_Params):
    """Threats that cover one target, highest confidence first."""

    target_id: str = Field(min_length=1)
    min_confidence: float | None = Field(default=None, ge=0, le=1)


class PlanAssignmentsParams(_Params):
    """Sorties in a plan with their aircraft, load config, times, and crew."""

    plan_id: str = Field(min_length=1)
    mission_id: str | None = Field(default=None, description="Only sorties for this mission.")


@dataclass(frozen=True)
class TemplateDef:
    params: type[BaseModel]
    cypher: str


_AIRCRAFT_WITH_QUALIFIED_CREW = """
MATCH (a:Aircraft)-[:LOCATED_AT]->(b:Base {base_id: $base_id})
WHERE a.status = $status
  AND ($aircraft_type IS NULL OR a.type = $aircraft_type)
MATCH (c:Crew)-[:QUALIFIED_ON]->(:AircraftType {type: a.type})
WHERE (c)-[:LOCATED_AT]->(b)
  AND ($max_fatigue IS NULL OR c.fatigue_score <= $max_fatigue)
RETURN a.tail_no AS tail_no,
       a.type AS aircraft_type,
       c.crew_id AS crew_id,
       c.role AS role,
       c.fatigue_score AS fatigue_score
ORDER BY tail_no, crew_id
LIMIT $limit
"""

_MISSIONS_FOR_TARGET = """
MATCH (m:Mission)-[:TARGETS]->(:Target {target_id: $target_id})
WHERE $status IS NULL OR m.status = $status
RETURN m.mission_id AS mission_id,
       m.type AS type,
       m.priority AS priority,
       m.status AS status,
       m.window_start AS window_start,
       m.window_end AS window_end
ORDER BY priority, mission_id
LIMIT $limit
"""

_ASSETS_AT_BASE = """
MATCH (b:Base {base_id: $base_id})
CALL (b) {
  MATCH (a:Aircraft)-[:LOCATED_AT]->(b)
  WHERE $asset_kind IS NULL OR $asset_kind = 'aircraft'
  RETURN 'aircraft' AS asset_kind, a.tail_no AS asset_id, a.type AS detail, null AS quantity
  UNION ALL
  MATCH (c:Crew)-[:LOCATED_AT]->(b)
  WHERE $asset_kind IS NULL OR $asset_kind = 'crew'
  RETURN 'crew' AS asset_kind, c.crew_id AS asset_id, c.role AS detail, null AS quantity
  UNION ALL
  MATCH (s:WeaponStock)-[:STORED_AT]->(b)
  WHERE $asset_kind IS NULL OR $asset_kind = 'weapon_stock'
  RETURN 'weapon_stock' AS asset_kind, s.stock_id AS asset_id, s.weapon_code AS detail, s.quantity AS quantity
  UNION ALL
  MATCH (q:Squadron)-[:BASED_AT]->(b)
  WHERE $asset_kind IS NULL OR $asset_kind = 'squadron'
  RETURN 'squadron' AS asset_kind, q.sqn_id AS asset_id, q.type AS detail, null AS quantity
}
RETURN asset_kind, asset_id, detail, quantity
ORDER BY asset_kind, asset_id
LIMIT $limit
"""

_THREATS_COVERING_TARGET = """
MATCH (th:Threat)-[:COVERS]->(:Target {target_id: $target_id})
WHERE $min_confidence IS NULL OR th.confidence >= $min_confidence
RETURN th.threat_id AS threat_id,
       th.type AS type,
       th.range_km AS range_km,
       th.confidence AS confidence,
       th.status AS status,
       th.last_seen AS last_seen
ORDER BY confidence DESC, threat_id
LIMIT $limit
"""

_PLAN_ASSIGNMENTS = """
MATCH (p:Plan {plan_id: $plan_id})-[r:ASSIGNS]->(a:Aircraft)
WHERE $mission_id IS NULL OR r.mission_id = $mission_id
OPTIONAL MATCH (p)-[cr:ASSIGNS_CREW]->(c:Crew)
WHERE cr.tail_no = a.tail_no AND cr.mission_id = r.mission_id
RETURN r.mission_id AS mission_id,
       r.sortie_no AS sortie_no,
       a.tail_no AS tail_no,
       r.load_config_id AS load_config_id,
       r.depart_time AS depart_time,
       r.tot AS tot,
       c.crew_id AS crew_id,
       cr.role AS crew_role
ORDER BY sortie_no, crew_id
LIMIT $limit
"""

TEMPLATES: dict[OntologyTemplate, TemplateDef] = {
    "aircraft_with_qualified_crew": TemplateDef(AircraftWithQualifiedCrewParams, _AIRCRAFT_WITH_QUALIFIED_CREW),
    "missions_for_target": TemplateDef(MissionsForTargetParams, _MISSIONS_FOR_TARGET),
    "assets_at_base": TemplateDef(AssetsAtBaseParams, _ASSETS_AT_BASE),
    "threats_covering_target": TemplateDef(ThreatsCoveringTargetParams, _THREATS_COVERING_TARGET),
    "plan_assignments": TemplateDef(PlanAssignmentsParams, _PLAN_ASSIGNMENTS),
}

assert set(TEMPLATES) == set(get_args(OntologyTemplate)), "TEMPLATES must cover OntologyTemplate exactly"


async def run_ontology_template(
    reader: "GraphReader",
    template: OntologyTemplate,
    params: dict[str, Any],
    *,
    timeout_s: float,
) -> list[dict[str, Any]]:
    definition = TEMPLATES.get(template)
    if definition is None:
        raise ToolInputError(f"unknown template: {template}")
    try:
        validated = definition.params.model_validate(params)
    except ValidationError as exc:
        raise ToolInputError(f"{template}: {_first_error(exc)}") from exc
    bound = {**validated.model_dump(), "limit": ROW_LIMIT}
    return await reader.read(definition.cypher, bound, timeout_s=timeout_s)


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"])
    if loc:
        return f"{loc}: {message}"
    return message
