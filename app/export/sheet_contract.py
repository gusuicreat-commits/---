from sqlalchemy import select

from app.export import finish
from app.models import Contract


def build(ws, session, ht_code) -> None:
    contract = session.scalar(select(Contract).where(Contract.ht_code == ht_code))
    ws.append(["合同号", "客户名称", "录入日期"])
    ws.append([str(contract.ht_code), contract.name, contract.date])
    ws["A2"].number_format = "@"
    ws["C2"].number_format = "yyyy-mm-dd"
    finish(ws)
