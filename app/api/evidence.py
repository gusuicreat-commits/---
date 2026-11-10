"""录音证据、时间线和主管复核接口。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.errors import NotFound
from app.fields import load_fields
from app.models import MODEL_BY_TABLE, VoiceLog
from app.services import contracts, records, voicelog
from app.voice import parser

router = APIRouter(prefix="/api")


@router.get("/voice/logs")
def logs(ht_code: str | None = None,
         status: Literal["ok", "review", "resolved", "ignored"] | None = None,
         limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
         session: Session = Depends(get_session)):
    query = select(VoiceLog)
    if ht_code is not None:
        query = query.where(VoiceLog.ht_code == ht_code)
    if status is not None:
        query = query.where(VoiceLog.status == status)
    if before_id is not None:
        query = query.where(VoiceLog.id < before_id)
    return {"items": [voicelog.as_dict(row) for row in session.scalars(
        query.order_by(VoiceLog.id.desc()).limit(limit))]}


@router.get("/voice/logs/{voice_id}")
def detail(voice_id: int, session: Session = Depends(get_session)):
    return voicelog.as_dict(voicelog.get(session, voice_id), detail=True)


@router.get("/voice/logs/{voice_id}/audio")
def audio(voice_id: int, session: Session = Depends(get_session)):
    row = voicelog.get(session, voice_id)
    root = get_settings().audio_dir.resolve()
    try:
        target = (root / row.audio_path).resolve() if row.audio_path else None
        if target is None or not target.is_relative_to(root) or not target.is_file():
            raise NotFound("这条记录没有录音", code="NO_AUDIO")
    except (OSError, ValueError):
        raise NotFound("这条记录没有录音", code="NO_AUDIO") from None
    media = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
             ".mp4": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg"}
    return FileResponse(target, media_type=media.get(target.suffix.lower(), "application/octet-stream"))


@router.get("/contracts/{ht_code}/timeline")
def timeline(ht_code: str, session: Session = Depends(get_session)):
    code = contracts.get(session, ht_code).ht_code
    rows = session.scalars(select(VoiceLog).where(VoiceLog.ht_code == code)).all()
    # 无合同行为的语音可以关联记录，也需要在该合同时间线中展示证据。
    linked_rows = session.scalars(select(VoiceLog).where(
        VoiceLog.ht_code.is_(None), VoiceLog.record_id.is_not(None))).all()
    linked = {}
    items = [{"type": "voice", "at": voicelog.as_dict(row)["created_at"],
              "voice": voicelog.as_dict(row)} for row in rows]
    for row in [*rows, *linked_rows]:
        if row.record_id is not None:
            linked.setdefault((row.record_kind, row.record_id), []).append(row.id)
    for table, model in MODEL_BY_TABLE.items():
        kind = load_fields()[table]["kind"]
        for row in session.scalars(select(model).where(model.ht_code == code)):
            record = records.as_dict(table, row)
            ids = sorted(linked.get((kind, row.id), []))
            items.append({"type": "record", "at": record["created_at"], "kind": kind,
                          "record": record, "voice_ids": ids})
            for voice in linked_rows:
                if voice.id in ids:
                    data = voicelog.as_dict(voice)
                    items.append({"type": "voice", "at": data["created_at"], "voice": data})
    items.sort(key=lambda item: (item["at"], item["type"] != "voice",
                                 item.get("voice", item.get("record"))["id"]))
    return {"items": items}


@router.get("/review")
def review(status: Literal["review", "resolved", "ignored", "all"] = "review",
           session: Session = Depends(get_session)):
    query = select(VoiceLog)
    if status != "all":
        query = query.where(VoiceLog.status == status)
    else:
        query = query.where(VoiceLog.status.in_(["review", "resolved", "ignored"]))
    return {"items": [voicelog.as_dict(row) for row in session.scalars(
        query.order_by(VoiceLog.id.desc()))]}


class Resolution(BaseModel):
    action: Literal["learn", "ignore"]
    wrong: str | None = None
    right: str | None = None
    note: str | None = None


@router.post("/review/{voice_id}/resolve")
def resolve(voice_id: int, body: Resolution, session: Session = Depends(get_session)):
    return voicelog.resolve(session, voice_id, **body.model_dump())


class Correction(BaseModel):
    wrong: str
    right: str


@router.get("/corrections")
def corrections(session: Session = Depends(get_session)):
    return {"builtin": parser.load_voice_config()["corrections"],
            "learned": voicelog.learned_list(session)}


@router.post("/corrections")
def add_correction(body: Correction, session: Session = Depends(get_session)):
    return voicelog.add_correction(session, body.wrong, body.right)


@router.delete("/corrections/{correction_id}", status_code=204)
def delete_correction(correction_id: int, session: Session = Depends(get_session)):
    voicelog.delete_correction(session, correction_id)
    return Response(status_code=204)
