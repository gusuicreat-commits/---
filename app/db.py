from functools import lru_cache

from fastapi import Request
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.fields import load_fields, validated_fields
from app.models import Base, Rule
from app.migrations import migrate_paint_primer_text


@lru_cache(maxsize=16)
def _engine(db_path: str):
    return create_engine(URL.create("sqlite", database=db_path), connect_args={"timeout": 5})


def get_engine():
    path = get_settings().db_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return _engine(str(path))


def SessionLocal(**kwargs) -> Session:
    return Session(bind=get_engine(), **kwargs)


def init_db(engine=None):
    engine = engine if engine is not None else get_engine()
    Base.metadata.create_all(engine)
    migrate_paint_primer_text(engine)
    with Session(engine) as session:
        existing = set(session.execute(select(Rule.table_name, Rule.name)).all())
        for table in load_fields():
            for field in validated_fields(table):
                if (table, field["name"]) not in existing:
                    session.add(Rule(table_name=table, name=field["name"]))
        session.commit()


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session
