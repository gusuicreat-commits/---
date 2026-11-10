import json
from collections.abc import Callable

from app.config import ROOT
from app.export import sheet_contract, sheet_drum, sheet_travel

GENERATORS = {"contract": sheet_contract.build, "drum": sheet_drum.build, "travel": sheet_travel.build}


def load_sheets() -> list[tuple[str, Callable]]:
    sheets = json.loads((ROOT / "config" / "sheets.json").read_text(encoding="utf-8"))
    return [(item["name"], GENERATORS[item["generator"]]) for item in sheets]
