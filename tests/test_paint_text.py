"""油漆改名、底漆文本免检及旧整数存储迁移。"""

from datetime import date
from io import BytesIO
import sqlite3

import openpyxl
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db import init_db
from app.models import TravelPaintParam
from app.errors import Invalid
from app.services import rules
from app.voice.parser import parse_text


@pytest.mark.parametrize("said,expected", [
    ("底漆厚度九十八、九十七、九十六", "98,97,96"),
    ("底漆厚度九十八，九十四九十六", "98,94,96"),
    ("底漆厚度九十九十九", None),
    ("底漆厚度98，94，96，面漆厚度99", "98,94,96"),
    ("底漆厚度98.5、97.25", "98.5,97.25"),
    ("底漆厚度98，不对，96", "96"),
    ("底漆厚度98，九十多", None),
    ("底漆厚度红色防锈漆", None),
    ("底漆厚度98.123", None),
])
def test_multi_readings(said, expected):
    result=parse_text(said,"travel_paint",date(2026,11,10))
    assert result['fields'].get('primer_thickness_ratio')==expected


@pytest.mark.parametrize('values,ok', [('98,97,96',1),('98,94,96',0),('95,100',1),('94,101,96',0)])
def test_per_reading_judgement(contract,client,values,ok):
    response=client.post(f'/api/contracts/{contract}/records/travel_paint',json={
        'primer_thickness_ratio':values,'total_thickness_ratio':99,'adhesion_loss_ratio':1})
    assert response.status_code==201
    result=response.json()
    assert result['record']['primer_thickness_ratio']==values
    assert result['validation']['is_qualified']==ok
    if not ok:
        assert '测点' in result['validation']['items'][0]['reason']
        assert '不合格' in result['validation']['items'][0]['reason']


def test_multi_readings_export(contract,client,good_drum,complete_new_parameters,tmp_path,monkeypatch):
    monkeypatch.setenv('QC_EXPORT_DIR',str(tmp_path/'exports'))
    complete_new_parameters(contract)
    client.post(f'/api/contracts/{contract}/records/drum',json=good_drum)
    client.post(f'/api/contracts/{contract}/records/travel_drive',json={'noise':80})
    for values,status in [('98,94,96',409),('98,97,96',200)]:
        client.post(f'/api/contracts/{contract}/records/travel_paint',json={
            'primer_thickness_ratio':values,'total_thickness_ratio':99,'adhesion_loss_ratio':1})
        assert client.post(f'/api/contracts/{contract}/export').status_code==status
    file=client.get('/api/exports').json()['items'][0]
    response=client.get(f"/api/exports/{contract}/{file['date'].replace('-','')}")
    w=openpyxl.load_workbook(BytesIO(response.content))
    assert w['检验汇总']['A21'].value=='98,97,96'
    w.close()


def legacy_engine(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE travel_paint_param (
                primer_thickness_ratio INTEGER, total_thickness_ratio INTEGER,
                adhesion_loss_ratio INTEGER, id INTEGER NOT NULL PRIMARY KEY,
                ht_code VARCHAR(11) NOT NULL, is_qualified INTEGER, created_at DATETIME NOT NULL);
            CREATE INDEX ix_travel_paint_param_ht_code ON travel_paint_param(ht_code);
            CREATE TABLE rule (id INTEGER PRIMARY KEY, table_name VARCHAR(64), name VARCHAR(64),
                               min INTEGER, max INTEGER, UNIQUE(table_name, name));
            INSERT INTO rule VALUES (1, 'travel_paint_param', 'primer_thickness_ratio', 9500, 10000);
            INSERT INTO travel_paint_param VALUES (9840, 9900, 100, 1, '20264827', 1, '2026-11-10 10:00:00');
            INSERT INTO travel_paint_param VALUES (NULL, 9900, 100, 2, '20260007', 0, '2026-11-10 11:00:00');
            INSERT INTO travel_paint_param VALUES (0, 9900, 100, 3, '20260008', NULL, '2026-11-10 12:00:00');
        ''')
    return create_engine(f"sqlite:///{path}")


def test_migration_preserves_visible_values_and_is_idempotent(tmp_path):
    engine = legacy_engine(tmp_path / "old.sqlite3")
    try:
        init_db(engine)
        init_db(engine)
        with engine.connect() as connection:
            assert dict((r[1], r[2]) for r in connection.exec_driver_sql(
                "PRAGMA table_info(travel_paint_param)"))["primer_thickness_ratio"] == "VARCHAR(255)"
            assert connection.exec_driver_sql(
                "SELECT id, primer_thickness_ratio, total_thickness_ratio, ht_code, is_qualified "
                "FROM travel_paint_param ORDER BY id").all() == [
                    (1, "98.4", 9900, "20264827", 1), (2, None, 9900, "20260007", 0),
                    (3, "0", 9900, "20260008", None)]
            assert "ix_travel_paint_param_ht_code" in [row[1] for row in connection.exec_driver_sql(
                "PRAGMA index_list(travel_paint_param)")]
        with Session(engine) as session:
            assert session.get(TravelPaintParam, 1).primer_thickness_ratio == "98.4"
            assert rules.list_rules(session)["total"] == 11
    finally:
        engine.dispose()


def test_migration_ddl_and_value_changes_roll_back_together(tmp_path):
    engine = legacy_engine(tmp_path / "rollback.sqlite3")

    def fail_before_drop(conn, cursor, statement, parameters, context, many):
        if "DROP COLUMN _primer_thickness_cents" in statement:
            raise RuntimeError("模拟迁移中断")

    event.listen(engine, "before_cursor_execute", fail_before_drop)
    try:
        with pytest.raises(RuntimeError, match="模拟迁移中断"):
            init_db(engine)
        with engine.connect() as connection:
            columns = dict((r[1], r[2]) for r in connection.exec_driver_sql("PRAGMA table_info(travel_paint_param)"))
            assert columns["primer_thickness_ratio"] == "INTEGER"
            assert "_primer_thickness_cents" not in columns
            assert connection.exec_driver_sql("SELECT primer_thickness_ratio FROM travel_paint_param WHERE id=1").scalar() == 9840
    finally:
        event.remove(engine, "before_cursor_execute", fail_before_drop)
        engine.dispose()
