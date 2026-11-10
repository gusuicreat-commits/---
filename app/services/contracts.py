from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError

from app import htcode, timeutil
from app.errors import Conflict, Invalid, NotFound
from app.fields import load_fields
from app.models import Contract, MODEL_BY_TABLE
from app.services.status import status_from_latest


def as_dict(contract) -> dict:
    return {"id": contract.id, "ht_code": contract.ht_code, "name": contract.name,
            "date": contract.date.isoformat()}


def suggestion(session):
    return htcode.suggest(session.scalars(select(Contract.ht_code)).all(), timeutil.today_bj())


def _name(raw):
    value = raw.strip()
    if not value:
        raise Invalid("NAME_EMPTY", "客户名称不能为空")
    if len(value) > 128:
        raise Invalid("NAME_TOO_LONG", "客户名称最多 128 个字")
    return value


def create(session, ht_code, name, date=None):
    ht_code = htcode.normalize(ht_code)
    error = htcode.check(ht_code)
    if error:
        raise Invalid(error.code, error.message)
    if session.scalar(select(Contract.id).where(Contract.ht_code == ht_code)) is not None:
        raise Conflict("DUPLICATE", "合同号已存在", suggestion=suggestion(session))
    contract = Contract(ht_code=ht_code, name=_name(name), date=date or timeutil.today_bj())
    session.add(contract)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.scalar(select(Contract.id).where(Contract.ht_code == ht_code)) is not None:
            raise Conflict("DUPLICATE", "合同号已存在", suggestion=suggestion(session)) from None
        raise
    return as_dict(contract)


def get(session, ht_code):
    contract = session.scalar(select(Contract).where(Contract.ht_code == htcode.normalize(ht_code)))
    if contract is None:
        raise NotFound("合同不存在，请先新建合同")
    return contract


def update(session, ht_code, name=None, date=None, new_ht_code=None):
    contract = get(session, ht_code)
    if new_ht_code and htcode.normalize(new_ht_code) != contract.ht_code:
        raise Invalid("HT_CODE_LOCKED", "合同号不能修改")
    if name is not None:
        contract.name = _name(name)
    if date is not None:
        contract.date = date
    session.commit()
    return as_dict(contract)


def delete(session, ht_code):
    contract = get(session, ht_code)
    for model in MODEL_BY_TABLE.values():
        if session.scalar(select(model.id).where(model.ht_code == contract.ht_code).limit(1)) is not None:
            raise Conflict("HAS_RECORDS", "已有参数记录，不能删除")
    session.delete(contract)
    session.commit()


def list(session, q=None, status=None):
    if status is not None and status not in {"未录全", "不合格", "待校验", "合格"}:
        raise Invalid("BAD_REQUEST", "请求格式不正确")
    query = select(Contract).order_by(Contract.ht_code.desc())
    if q:
        query = query.where(or_(Contract.ht_code.contains(q, autoescape=True),
                               Contract.name.contains(q, autoescape=True),
                               cast(Contract.date, String).contains(q, autoescape=True)))
    contracts = session.scalars(query).all()
    if not contracts:
        return []
    # 每类一次批量查询最新记录，列表查询数量不随合同数增长。
    codes = [contract.ht_code for contract in contracts]
    latest_by_table = {}
    for table, model in MODEL_BY_TABLE.items():
        ids = select(func.max(model.id)).where(model.ht_code.in_(codes)).group_by(model.ht_code)
        latest_by_table[table] = {record.ht_code: record for record in
                                  session.scalars(select(model).where(model.id.in_(ids)))}
    items = []
    for contract in contracts:
        current = status_from_latest({load_fields()[table]["kind"]: rows.get(contract.ht_code)
                                      for table, rows in latest_by_table.items()})
        if status is None or current["status"] == status:
            items.append({**as_dict(contract), "status": current["status"],
                          "status_reason": current["reason"]})
    return items
