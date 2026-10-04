from pathlib import Path

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.viz import render_html, scenario_payload

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


def test_viz_page_embeds_every_domain() -> None:
    scenario = build_scenario(load_config(CONFIG_PATH), 42)
    payload = scenario_payload(scenario)
    for key in (
        "bases", "borders", "features", "zones", "threats", "tracks", "targets", "missions",
        "weather", "observations", "adsb", "aircraft", "crew", "weapons", "stocks", "load_configs",
    ):
        assert payload[key], key
    html = render_html(scenario)
    assert "const DATA = {" in html
    assert "__DATA__" not in html
    assert "__CARTO_KEY__" not in html


def test_scenario_has_sar_and_all_aircraft_statuses() -> None:
    scenario = build_scenario(load_config(CONFIG_PATH), 42)
    assert "SAR" in {m.type for m in scenario.missions}
