import logging

from sqlalchemy import select

from app.dec import fmt
from app.fields import validated_fields
from app.models import MODEL_BY_TABLE, Rule
from app.services import contracts
from app.services.status import contract_status


def judge(table, record_values: dict, rules: dict[str, Rule]) -> dict:
    items = []
    for field in validated_fields(table):
        name, label, unit = field["name"], field["label"], field.get("unit", "")
        value, rule = record_values.get(name), rules.get(name)
        lower, upper = (rule.min, rule.max) if rule is not None else (None, None)
        reason = None
        if value is None:
            reason = f"{label}未填写"
        elif lower is None or upper is None:
            reason = f"规则未配置：{label}"
        elif value < lower or value > upper:
            reason = f"{label} {fmt(value)} {unit}，超出 {fmt(lower)}～{fmt(upper)}"
        items.append({"field": name, "label": label, "unit": unit,
                      "value": fmt(value) if value is not None else None,
                      "min": fmt(lower) if lower is not None else None,
                      "max": fmt(upper) if upper is not None else None,
                      "ok": reason is None, "reason": reason})
    failures = [item["reason"] for item in items if not item["ok"]]
    return {"is_qualified": int(not failures), "items": items, "failures": failures}


def validate_record(session, table, record) -> dict:
    # 显式刷新规则，避免同一 Session 的身份缓存保留旧上下限。
    rules = session.scalars(select(Rule).where(Rule.table_name == table)
                            .execution_options(populate_existing=True)).all()
    result = judge(table, {field["name"]: getattr(record, field["name"])
                           for field in validated_fields(table)}, {rule.name: rule for rule in rules})
    record.is_qualified = result["is_qualified"]
    session.commit()
    return result


def run_pending(session) -> dict:
    result = {"processed": 0, "qualified": 0, "unqualified": 0, "failed": 0}
    for table, model in MODEL_BY_TABLE.items():
        ids = session.scalars(select(model.id).where(model.is_qualified.is_(None)).order_by(model.id)).all()
        for record_id in ids:
            try:
                record = session.get(model, record_id)
                verdict = validate_record(session, table, record)
                result["processed"] += 1
                result["qualified" if verdict["is_qualified"] else "unqualified"] += 1
            except Exception:
                session.rollback()
                result["failed"] += 1
                logging.exception("补跑校验失败：%s，记录 %s", table, record_id)
    return result


def revalidate_contract(session, ht_code):
    ht_code = contracts.get(session, ht_code).ht_code
    count = 0
    for table, model in MODEL_BY_TABLE.items():
        ids = session.scalars(select(model.id).where(model.ht_code == ht_code).order_by(model.id)).all()
        for record_id in ids:
            validate_record(session, table, session.get(model, record_id))
            count += 1
    return {"records": count, "contract_status": contract_status(session, ht_code)}
