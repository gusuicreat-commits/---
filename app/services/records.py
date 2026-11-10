import logging
from typing import TypedDict

from sqlalchemy import select

from app import htcode, timeutil
from app.dec import fmt, to_dec2
from app.errors import BadNumber, Invalid, TooManyDecimals
from app.fields import load_fields, table_by_kind
from app.models import MODEL_BY_TABLE
from app.services import contracts, validation
from app.services.status import contract_status


class SubmitResult(TypedDict):
    record: dict
    validation: dict | None
    warning: str | None
    contract_status: dict


def as_dict(table, record):
    result = {"id": record.id, "ht_code": record.ht_code,
              "is_qualified": record.is_qualified,
              "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S")}
    for field in load_fields()[table]["fields"]:
        value = getattr(record, field["name"])
        result[field["name"]] = fmt(value) if value is not None and field["type"] == "number" else value
    return result


def submit(session, kind, ht_code, payload: dict) -> SubmitResult:
    table = table_by_kind(kind)
    ht_code = htcode.normalize(ht_code)
    contracts.get(session, ht_code)
    values, errors = {}, {}
    for field in load_fields()[table]["fields"]:
        name, label = field["name"], field["label"]
        raw = payload.get(name)
        if field["type"] == "number":
            try:
                values[name] = to_dec2(raw)
            except BadNumber:
                errors[name] = f"{label}必须是数字"
            except TooManyDecimals:
                errors[name] = f"{label}最多两位小数"
        elif raw is None:
            values[name] = None
        elif not isinstance(raw, str):
            errors[name] = f"{label}必须是文本"
        else:
            values[name] = raw.strip() or None
            if len(raw.strip()) > field["max_len"]:
                errors[name] = f"{label}最多 {field['max_len']} 个字"
    if errors:
        raise Invalid("FIELD_INVALID", "参数填写有误", fields=errors)
    record = MODEL_BY_TABLE[table](ht_code=ht_code, **values, is_qualified=None,
                                    created_at=timeutil.now_bj().replace(tzinfo=None))
    session.add(record)
    session.commit()
    result, warning = None, None
    try:
        result = validation.validate_record(session, table, record)
    except Exception:
        session.rollback()
        logging.exception("参数已保存，但校验失败")
        warning = "已保存，校验未完成，可稍后补跑校验"
    return {"record": as_dict(table, record), "validation": result, "warning": warning,
            "contract_status": contract_status(session, ht_code)}


def history(session, kind, ht_code):
    table = table_by_kind(kind)
    ht_code = htcode.normalize(ht_code)
    contracts.get(session, ht_code)
    model = MODEL_BY_TABLE[table]
    return [as_dict(table, record) for record in session.scalars(
        select(model).where(model.ht_code == ht_code).order_by(model.id.desc()))]
