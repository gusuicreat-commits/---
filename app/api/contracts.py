from datetime import date as Date

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import contracts, records, rules
from app.services.status import latest_records, status_from_latest
from app.fields import table_by_kind

router = APIRouter(prefix="/api/contracts")


class CreateContract(BaseModel):
    ht_code: str
    name: str
    date: Date | None = None


class UpdateContract(BaseModel):
    name: str | None = None
    date: Date | None = None
    ht_code: str | None = None


@router.get("/suggest")
def suggest(session: Session = Depends(get_session)):
    return {"ht_code": contracts.suggestion(session)}


@router.post("", status_code=201)
def create(body: CreateContract, session: Session = Depends(get_session)):
    return contracts.create(session, **body.model_dump())


@router.get("")
def list_contracts(q: str | None = None, status: str | None = None,
                   session: Session = Depends(get_session)):
    return {"items": contracts.list(session, q=q, status=status)}


@router.get("/{ht_code}")
def detail(ht_code: str, session: Session = Depends(get_session)):
    contract = contracts.get(session, ht_code)
    latest = latest_records(session, contract.ht_code)
    status = status_from_latest(latest)
    progress = rules.list_rules(session)
    return {"contract": contracts.as_dict(contract), "status": status["status"],
            "status_reason": status["reason"],
            "latest": {kind: records.as_dict(table_by_kind(kind), record) if record else None
                       for kind, record in latest.items()},
            "rules": {"configured": progress["configured"], "total": progress["total"]}}


@router.patch("/{ht_code}")
def update(ht_code: str, body: UpdateContract, session: Session = Depends(get_session)):
    return contracts.update(session, ht_code, name=body.name, date=body.date, new_ht_code=body.ht_code)


@router.delete("/{ht_code}", status_code=204)
def delete(ht_code: str, session: Session = Depends(get_session)):
    contracts.delete(session, ht_code)
    return Response(status_code=204)
