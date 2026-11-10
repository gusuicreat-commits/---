from sqlalchemy import select

from app.fields import load_fields
from app.models import MODEL_BY_TABLE


def latest_records(session, ht_code) -> dict:
    return {load_fields()[table]["kind"]: session.scalar(
        select(model).where(model.ht_code == ht_code).order_by(model.id.desc()).limit(1))
        for table, model in MODEL_BY_TABLE.items()}


def status_from_latest(latest: dict) -> dict:
    missing = [meta["label"] for meta in load_fields().values() if latest[meta["kind"]] is None]
    if missing:
        return {"status": "未录全", "reason": "缺少" + "和".join(label + "参数" for label in missing)}
    bad = [meta["label"] for meta in load_fields().values()
           if latest[meta["kind"]].is_qualified == 0]
    if bad:
        reason = bad[0] + "参数不合格" if len(bad) == 1 else "和".join(bad) + "参数都不合格"
        return {"status": "不合格", "reason": reason}
    if any(record.is_qualified is None for record in latest.values()):
        return {"status": "待校验", "reason": "有记录尚未校验"}
    return {"status": "合格", "reason": "可以导出"}


def contract_status(session, ht_code) -> dict:
    return status_from_latest(latest_records(session, ht_code))
