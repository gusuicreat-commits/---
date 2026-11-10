import logging
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

from app.errors import AppError
from app.export.registry import load_sheets
from app.models import Contract
from app.services import contracts, status
from app.storage import get_storage
from app.timeutil import today_bj


def export_one(session, ht_code) -> dict:
    contract = contracts.get(session, ht_code)
    ht_code = contract.ht_code
    current = status.contract_status(session, ht_code)
    if current["status"] != "合格":
        raise AppError(409, "NOT_QUALIFIED",
                       f"合同状态为「{current['status']}」：{current['reason']}，不能导出",
                       {"status": current["status"]})
    wb = Workbook()
    wb.remove(wb.active)
    sheets = []
    sections = []
    for name, generator in load_sheets():
        ws = wb.create_sheet(name)
        error = None
        try:
            # 隔离生成器的数据库异常，避免影响后续工作表的查询。
            with session.begin_nested():
                generated = generator(ws, session, ht_code)
                if generated:
                    sections.extend(generated)
                    errors = [part['error'] for part in generated if not part['ok']]
                    if errors:
                        error = '；'.join(errors)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logging.exception("工作表「%s」生成失败", name)
            for merged in list(ws.merged_cells.ranges):
                ws.unmerge_cells(str(merged))
            ws.delete_rows(1, ws.max_row)
            ws["A1"] = "生成失败"
            ws["A2"] = f"原因：{error}"
            ws["A2"].data_type = "s"
        sheets.append({"name": name, "ok": error is None, "error": error})
    day = today_bj()
    filename = f"{ht_code}_{day:%Y%m%d}.xlsx"
    rel = f"{day:%Y-%m}/{filename}"
    try:
        with BytesIO() as stream:
            wb.save(stream)
            get_storage().save(rel, stream.getvalue())
    finally:
        wb.close()
    return {"ht_code": ht_code, "file": filename, "rel": rel, "sheets": sheets, "sections": sections}


def export_all(session) -> dict:
    result = {"succeeded": [], "failed": [], "skipped": 0}
    codes = session.scalars(select(Contract.ht_code).order_by(Contract.ht_code)).all()
    for code in codes:
        try:
            if status.contract_status(session, code)["status"] != "合格":
                result["skipped"] += 1
                continue
            result["succeeded"].append(export_one(session, code))
        except Exception as exc:
            session.rollback()
            logging.exception("合同「%s」导出失败", code)
            result["failed"].append({"ht_code": code, "reason": str(exc)})
    return result
