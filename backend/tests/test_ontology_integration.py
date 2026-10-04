import asyncio
import os
from datetime import timedelta
from pathlib import Path

import pytest

from garuda.config import load_config
from garuda.contracts.plan import Assignment, CrewAssignment, PlanDraft, SortieSlot
from garuda.generators.missions import build_scenario
from garuda.queries.ontology import run_ontology_template

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"

pytestmark = pytest.mark.skipif(
    os.environ.get("GARUDA_INTEGRATION") != "1",
    reason="set GARUDA_INTEGRATION=1 with the Compose stack running",
)


def test_templates_against_neo4j() -> None:
    asyncio.run(_templates_against_neo4j())


async def _templates_against_neo4j() -> None:
    from garuda.storage.neo4j_read import Neo4jGraphReader
    from garuda.storage.neo4j_sync import sync_neo4j, sync_plan
    from garuda.storage.seed import neo4j_settings

    scenario = build_scenario(load_config(CONFIG_PATH), 42)
    await sync_neo4j(*neo4j_settings(), scenario)

    airframe, member = next(
        (airframe, member)
        for airframe in scenario.aircraft
        if airframe.status == "FMC"
        for member in scenario.crew
        if member.base_id == airframe.base_id and airframe.type in member.qualifications
    )
    mission = next(mission for mission in scenario.missions if mission.target_id is not None)
    defended = next(target for target in scenario.targets if target.defended_by)
    depart = mission.window_start
    plan = PlanDraft(
        plan_id="P-ONTOLOGY-TEST",
        scenario_time=depart,
        assignments=[
            Assignment(
                tail_no=airframe.tail_no,
                config_id="CFG-TEST",
                mission_id=mission.mission_id,
                depart=depart,
                tot=depart + timedelta(hours=1),
            )
        ],
        sortie_slots=[
            SortieSlot(
                slot_id="S-1",
                mission_id=mission.mission_id,
                tail_no=airframe.tail_no,
                role=member.role,
                aircraft_type=airframe.type,
                start=depart,
                end=depart + timedelta(hours=2),
            )
        ],
        crew_assignments=[CrewAssignment(crew_id=member.crew_id, slot_id="S-1", role=member.role)],
    )
    await sync_plan(*neo4j_settings(), plan)
    await sync_plan(*neo4j_settings(), plan)

    reader = Neo4jGraphReader.from_env()
    try:
        paired = await run_ontology_template(
            reader, "aircraft_with_qualified_crew", {"base_id": airframe.base_id}, timeout_s=10
        )
        assert any(row["tail_no"] == airframe.tail_no and row["crew_id"] == member.crew_id for row in paired)

        missions = await run_ontology_template(
            reader, "missions_for_target", {"target_id": mission.target_id}, timeout_s=10
        )
        assert mission.mission_id in {row["mission_id"] for row in missions}

        assets = await run_ontology_template(reader, "assets_at_base", {"base_id": airframe.base_id}, timeout_s=10)
        assert {"aircraft", "crew"} <= {row["asset_kind"] for row in assets}
        only_crew = await run_ontology_template(
            reader, "assets_at_base", {"base_id": airframe.base_id, "asset_kind": "crew"}, timeout_s=10
        )
        assert only_crew and {row["asset_kind"] for row in only_crew} == {"crew"}

        threats = await run_ontology_template(
            reader, "threats_covering_target", {"target_id": defended.target_id}, timeout_s=10
        )
        assert {row["threat_id"] for row in threats} == set(defended.defended_by)

        sorties = await run_ontology_template(reader, "plan_assignments", {"plan_id": plan.plan_id}, timeout_s=10)
        assert len(sorties) == 1
        assert sorties[0]["tail_no"] == airframe.tail_no
        assert sorties[0]["crew_id"] == member.crew_id

        with pytest.raises(Exception):
            await reader.read("CREATE (:Probe {id: 1})", {}, timeout_s=5)
    finally:
        await reader.close()
