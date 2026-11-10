import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import htcode
from app.db import get_session
from app.errors import AppError, NotFound
from app.services import export
from app.storage import get_storage

router = APIRouter(prefix="/api")
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/contracts/{ht_code}/export")
def export_one(ht_code: str, session: Session = Depends(get_session)):
    return export.export_one(session, ht_code)


@router.post("/export/all")
def export_all(session: Session = Depends(get_session)):
    return export.export_all(session)


@router.get("/exports")
def list_exports():
    items = []
    for item in get_storage().list():
        match = re.fullmatch(r"(.+)_(\d{8})\.xlsx", PurePosixPath(item["rel"]).name, re.ASCII)
        code, day = None, None
        if match:
            try:
                day = datetime.strptime(match[2], "%Y%m%d").date().isoformat()
                code = match[1]
            except ValueError:
                pass
        items.append({**item, "ht_code": code, "date": day})
    return {"items": items}


@router.get("/exports/{ht_code}/{yyyymmdd}")
def download(ht_code: str, yyyymmdd: str):
    if htcode.check(ht_code) is not None or not re.fullmatch(r"\d{8}", yyyymmdd, re.ASCII):
        raise AppError(400, "BAD_PATH", "路径不合法")
    try:
        day = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    except ValueError:
        raise AppError(400, "BAD_PATH", "路径不合法") from None
    filename = f"{ht_code}_{yyyymmdd}.xlsx"
    rel = f"{day:%Y-%m}/{filename}"
    storage = get_storage()
    if not storage.exists(rel):
        raise NotFound("文件不存在，请先导出", code="FILE_NOT_FOUND")
    return FileResponse(storage.path(rel), media_type=XLSX_TYPE, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})
