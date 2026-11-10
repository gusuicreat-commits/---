from sqlalchemy import select

from app.dec import fmt, to_dec2
from app.errors import BadNumber, Invalid, NotFound, TooManyDecimals
from app.fields import field_meta, load_fields, validated_fields
from app.models import Rule


def as_dict(rule):
    field = field_meta(rule.table_name, rule.name)
    return {"id": rule.id, "table_name": rule.table_name,
            "table_label": load_fields()[rule.table_name]["label"],
            "name": rule.name, "label": field["label"], "unit": field.get("unit", ""),
            "min": fmt(rule.min) if rule.min is not None else None,
            "max": fmt(rule.max) if rule.max is not None else None,
            "configured": rule.min is not None and rule.max is not None}


def list_rules(session):
    rows = {(rule.table_name, rule.name): rule for rule in session.scalars(select(Rule))}
    items = [as_dict(rows[table, field["name"]]) for table in load_fields()
             for field in validated_fields(table)]
    return {"items": items, "configured": sum(item["configured"] for item in items), "total": len(items)}


def update_rule(session, rule_id, min, max):
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise NotFound("规则不存在")
    if rule.table_name not in load_fields() or rule.name not in {
            field["name"] for field in validated_fields(rule.table_name)}:
        raise Invalid("RULE_FIELD_UNKNOWN", "该参数不在校验字段清单中")
    values = []
    for raw, label in [(min, "下限"), (max, "上限")]:
        try:
            values.append(to_dec2(raw))
        except BadNumber:
            raise Invalid("FIELD_INVALID", f"{label}必须是数字") from None
        except TooManyDecimals:
            raise Invalid("FIELD_INVALID", "最多两位小数") from None
    lower, upper = values
    if lower is not None and upper is not None and lower > upper:
        raise Invalid("RULE_MIN_GT_MAX", "下限不能大于上限")
    rule.min, rule.max = lower, upper
    session.commit()
    return as_dict(rule)
