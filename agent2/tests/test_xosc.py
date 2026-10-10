from __future__ import annotations

import hashlib
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from app.cut_in.xosc import XoscExportError, render_xosc
from test_cut_in import plan
from test_llm import variant
from test_sample import snapshot

SCHEMA = Path(__file__).resolve().parents[1] / "knowledge/schemas/OpenSCENARIO_1_0.xsd"


def test_exports_deterministic_xosc_using_site_lanes_and_plan() -> None:
    selected, sampled, proposed = snapshot("Town01", 1), variant(), plan()
    xml, digest = render_xosc(proposed, sampled, selected)
    root = ET.fromstring(xml)
    positions = {node.get("entityRef"): node.find("PrivateAction/TeleportAction/Position/LanePosition")
                 for node in root.findall("Storyboard/Init/Actions/Private")}
    assert root.find("RoadNetwork/LogicFile").get("filepath") == "Town01"
    assert positions["hero"].attrib == {"roadId": "1", "laneId": "-1", "s": "10", "offset": "0"}
    assert positions["Motorcycle"].attrib == {"roadId": "1", "laneId": "-2", "s": "20", "offset": "0"}
    assert root.find(".//RelativeTargetLane").get("value") == "1"
    assert root.find(".//LaneChangeActionDynamics").get("dynamicsDimension") == "distance"
    assert root.find(".//RoadCondition").get("frictionScaleFactor") == "0.6"
    assert root.find(".//Precipitation").get("precipitationType") == "rain"
    assert root.find(".//Private[@entityRef='hero']//AbsoluteTargetSpeed").get("value") == "10.277778"
    assert digest == hashlib.sha256(xml.encode()).hexdigest()
    assert render_xosc(proposed, sampled, selected) == (xml, digest)


def test_decreasing_s_and_right_change_use_correct_signs() -> None:
    selected, sampled = snapshot("Town01", 1), variant()
    selected.catalog.cut_in_sites[0].ego_s = 70
    selected.catalog.cut_in_sites[0].motorcycle_s = 70
    selected.catalog.cut_in_sites[0].s_direction = -1
    selected.catalog.cut_in_sites[0].target_side = "right"
    sampled.site.ego_s = 70
    sampled.site.motorcycle_s = 70
    sampled.site.s_direction = -1
    sampled.site.target_side = "right"
    root = ET.fromstring(render_xosc(plan(), sampled, selected)[0])
    motorcycle = root.find(".//Private[@entityRef='Motorcycle']/PrivateAction/TeleportAction/Position/LanePosition")
    assert motorcycle.get("s") == "60"
    assert root.find(".//RelativeTargetLane").get("value") == "-1"


def test_legacy_site_requires_resync_before_export() -> None:
    selected, sampled = snapshot("Town01", 1), variant()
    selected.catalog.cut_in_sites[0].ego_s = None
    sampled.site.ego_s = None
    with pytest.raises(XoscExportError, match="SITE_POSITION_UNAVAILABLE"):
        render_xosc(plan(), sampled, selected)


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint is not installed")
def test_xosc_validates_against_open_scenario_1_0_schema(tmp_path: Path) -> None:
    xml, _ = render_xosc(plan(), variant(), snapshot("Town01", 1))
    path = tmp_path / "scenario.xosc"
    path.write_text(xml, encoding="utf-8")
    result = subprocess.run(["xmllint", "--noout", "--schema", str(SCHEMA), str(path)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_scenario_runner_gets_criteria_apart_from_the_end_condition() -> None:
    root = ET.fromstring(render_xosc(plan(), variant(), snapshot("Town01", 1))[0])
    end, criteria = root.findall("Storyboard/StopTrigger/ConditionGroup")
    assert [item.get("name") for item in end.findall("Condition")] == ["EndScenario"]
    names = [item.get("name") for item in criteria.findall("Condition")]
    assert "criteria_CollisionTest" in names and all(name.startswith("criteria_") for name in names)


def test_scenario_runner_recognises_the_ego() -> None:
    root = ET.fromstring(render_xosc(plan(), variant(), snapshot("Town01", 1))[0])
    types = {item.get("name"): item.find("Vehicle/Properties/Property[@name='type']").get("value")
             for item in root.findall("Entities/ScenarioObject")}
    assert types == {"hero": "ego_vehicle", "Motorcycle": "simulation"}


def test_every_scenario_lasts_five_seconds_of_simulation() -> None:
    root = ET.fromstring(render_xosc(plan(), variant(), snapshot("Town01", 1))[0])
    act_end = root.find("Storyboard/Story/Act/StopTrigger//SimulationTimeCondition").get("value")
    story_end = root.find("Storyboard/StopTrigger/ConditionGroup/Condition/ByValueCondition/SimulationTimeCondition").get("value")
    assert act_end == story_end == "5"
