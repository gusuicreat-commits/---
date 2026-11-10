import json
from collections.abc import Callable

from app.config import ROOT
from app.export import sheet_contract, sheet_drum, sheet_travel, sheet_control_cabinet, sheet_travel_paint
from app.export import sheet_summary

GENERATORS = {"contract": sheet_contract.build, "drum": sheet_drum.build, "travel": sheet_travel.build,
              "control_cabinet": sheet_control_cabinet.build, "travel_paint": sheet_travel_paint.build,
              "summary": sheet_summary.build}


def load_sheets() -> list[tuple[str, Callable]]:
    sheets = json.loads((ROOT / "config" / "sheets.json").read_text(encoding="utf-8"))
    return [(item["name"], GENERATORS[item["generator"]]) for item in sheets]
