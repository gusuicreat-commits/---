from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.events import bus
from app.services import rules

router = APIRouter(prefix="/api/rules")


class RuleInput(BaseModel):
    min: Any
    max: Any


@router.get("")
def list_rules(session: Session = Depends(get_session)):
    return rules.list_rules(session)


@router.put("/{id}")
def update_rule(id: int, body: RuleInput, session: Session = Depends(get_session),
                source: str | None = None):
    result = rules.update_rule(session, id, body.min, body.max)
    bus.publish("rules", {"rule": result}, source)
    return result
