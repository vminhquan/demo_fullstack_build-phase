"""Thêm tiêu chí đánh giá (criteria_*) vào StopTrigger của một file XOSC do Scenario Forge sinh.

ScenarioRunner chỉ đánh giá các Condition có tên bắt đầu bằng "criteria_"; không có thì in
"Nothing to analyze" và luôn coi là đạt.

    python add_criteria.py sf_brake.xosc            # ghi ra sf_brake.criteria.xosc
    python add_criteria.py in.xosc out.xosc CollisionTest KeepLaneTest
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_CRITERIA = ("CollisionTest",)


def add_criteria(xosc: str, names: tuple[str, ...] = DEFAULT_CRITERIA) -> str:
    root = ET.fromstring(xosc)
    group = root.find("./Storyboard/StopTrigger/ConditionGroup")
    if group is None:
        raise ValueError("XOSC không có Storyboard/StopTrigger/ConditionGroup")
    existing = {cond.get("name") for cond in group.iter("Condition")}
    for name in names:
        if f"criteria_{name}" in existing:
            continue
        cond = ET.SubElement(group, "Condition", {"name": f"criteria_{name}", "delay": "0", "conditionEdge": "rising"})
        ET.SubElement(ET.SubElement(cond, "ByValueCondition"), "ParameterCondition",
                      {"parameterRef": "", "value": "", "rule": "lessThan"})
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else source.with_suffix(".criteria.xosc")
    names = tuple(sys.argv[3:]) or DEFAULT_CRITERIA
    target.write_text(add_criteria(source.read_text(encoding="utf-8"), names), encoding="utf-8")
    print(f"Đã ghi {target} với criteria: {', '.join(names)}")


if __name__ == "__main__":
    main()
