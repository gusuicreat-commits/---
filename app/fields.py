from functools import lru_cache
import json

from app.config import ROOT
from app.errors import NotFound


@lru_cache(maxsize=1)
def load_fields() -> dict:
    return json.loads((ROOT / "config" / "fields.json").read_text(encoding="utf-8"))


def table_by_kind(kind) -> str:
    for table, meta in load_fields().items():
        if meta["kind"] == kind:
            return table
    raise NotFound("参数类别不存在")


def validated_fields(table) -> list[dict]:
    return [field for field in load_fields()[table]["fields"] if field["validate"]]


def field_meta(table, name) -> dict | None:
    return next((field for field in load_fields().get(table, {}).get("fields", [])
                 if field["name"] == name), None)
