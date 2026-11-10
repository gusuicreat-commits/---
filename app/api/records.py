from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.events import bus
from app.services import records, voicelog

router = APIRouter(prefix="/api/contracts")


@router.post("/{ht_code}/records/{kind}", status_code=201)
def submit(ht_code: str, kind: str, body: dict, session: Session = Depends(get_session),
           source: str | None = None, voice_ids: str | None = None):
    return voicelog.submit_record(session, kind, ht_code, body, voice_ids, source, event_bus=bus)


@router.get("/{ht_code}/records/{kind}")
def history(ht_code: str, kind: str, session: Session = Depends(get_session)):
    return {"items": records.history(session, kind, ht_code)}
