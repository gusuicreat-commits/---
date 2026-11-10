from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import validation

router = APIRouter(prefix="/api")


@router.post("/validation/pending")
def pending(session: Session = Depends(get_session)):
    return validation.run_pending(session)


@router.post("/contracts/{ht_code}/revalidate")
def revalidate(ht_code: str, session: Session = Depends(get_session)):
    return validation.revalidate_contract(session, ht_code)
