"""四类参数的真实业务闭环，以及同义词/单位解析回归。阈值均为测试值。"""
from datetime import date
from io import BytesIO
import sqlite3

import httpx
import openpyxl
import pytest
from sqlalchemy import inspect

from app.api import voice
from app.db import get_engine, init_db
from app.voice.parser import parse_text


@pytest.mark.parametrize('text', [
    '噪声八十二分贝', '噪音八十二分贝', '噪声82db', '噪音 82 dB',
    '噪声８２ＤＢ', '噪音82DB', '分贝八十二', 'db82', 'DB 八十二',
    '噪声分贝八十二', '噪音是分贝八十二', '噪音82 d b',
    '燥声八十二分贝', '造声八十二分贝',
])
def test_noise_aliases_and_units(text):
    result = parse_text(text, 'travel_drive', date(2026, 11, 10))
    assert result['fields'] == {'noise': '82'}
    assert result['rejected'] == []


@pytest.mark.parametrize('form,text,expected', [
    ('control_cabinet', '绝缘电阻五百兆欧，控制线力矩一点二牛米，电气间隙八毫米',
     {'insulation_resistance': '500', 'control_wire_torque': '1.2', 'electrical_clearance': '8'}),
    ('control_cabinet', '绝缘电阻500 MΩ，控制线扭矩1.2 N·m，电气间距0.8厘米',
     {'insulation_resistance': '500', 'control_wire_torque': '1.2', 'electrical_clearance': '8'}),
    ('control_cabinet', '绝缘电阻1000千欧', {'insulation_resistance': '1'}),
    ('control_cabinet', '绝缘电阻1000000欧姆', {'insulation_resistance': '1'}),
    ('travel_paint', '底漆厚度达标率百分之九十八，漆膜总厚度达标率99%，附着力脱落面积占比百分之一',
     {'primer_thickness_ratio': '98', 'total_thickness_ratio': '99', 'adhesion_loss_ratio': '1'}),
    ('travel_paint', '底漆达标率98，不对，99，脱落率0.5%',
     {'primer_thickness_ratio': '99', 'adhesion_loss_ratio': '0.5'}),
])
def test_new_parameter_voice(form, text, expected):
    result = parse_text(text, form, date(2026, 11, 10))
    assert result['fields'] == expected
    assert not result['rejected']


@pytest.mark.parametrize('form,text', [
    ('control_cabinet', '绝缘电阻八分贝'), ('control_cabinet', '控制线力矩8mm'),
    ('travel_paint', '脱落率1米'),
    ('drum', '噪音八十二分贝'), ('control_cabinet', '今天先不测，稍后再来'),
])
def test_wrong_units_ambiguous_and_other_forms_are_not_filled(form, text):
    assert parse_text(text, form, date(2026, 11, 10))['fields'] == {}


def test_upgrade_old_database_preserves_rules_and_contracts(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE contract (id INTEGER PRIMARY KEY, ht_code VARCHAR(11) NOT NULL UNIQUE,
                                   name VARCHAR(128) NOT NULL, date DATE NOT NULL);
            CREATE TABLE rule (id INTEGER PRIMARY KEY, table_name VARCHAR(64), name VARCHAR(64),
                               min INTEGER, max INTEGER, UNIQUE(table_name, name));
            INSERT INTO contract VALUES (1, '20261110-10', '原合同（样例）', '2026-11-10');
            INSERT INTO rule VALUES (1, 'drum_param', 'diameter', 124000, 126000);
        ''')
    monkeypatch.setenv('QC_DB_PATH', str(path))
    engine = get_engine()
    try:
        init_db(engine)
        init_db(engine)
        assert {'control_cabinet_param', 'travel_paint_param'} <= set(inspect(engine).get_table_names())
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT name FROM contract').fetchone()[0] == '原合同（样例）'
            assert db.execute('SELECT min,max FROM rule WHERE id=1').fetchone() == (124000, 126000)
            assert db.execute('SELECT count(*) FROM rule').fetchone()[0] == 11
            assert db.execute("SELECT count(*) FROM rule WHERE table_name IN ('control_cabinet_param','travel_paint_param') AND min IS NULL AND max IS NULL").fetchone()[0] == 6
    finally:
        engine.dispose()


def test_four_required_tables_voice_evidence_retest_and_export(configured, contract, good_drum, tmp_path, monkeypatch):
    client = configured
    monkeypatch.setenv('QC_EXPORT_DIR', str(tmp_path / 'exports'))
    for kind, values in [('drum', good_drum), ('travel_drive', {'noise': 82})]:
        assert client.post(f'/api/contracts/{contract}/records/{kind}', json=values).status_code == 201
    assert client.post(f'/api/contracts/{contract}/export').status_code == 409
    for form, text in [
        ('control_cabinet', '绝缘电阻五百兆欧，控制线力矩一点二牛米，电气间隙八毫米'),
        ('travel_paint', '底漆达标率98%，总厚度达标率99%，脱落率1%'),
    ]:
        parsed = client.post('/api/voice/parse-text', json={
            'text': text, 'form': form, 'ht_code': contract, 'source': 'phone'}).json()
        saved = client.post(f'/api/contracts/{contract}/records/{form}?voice_ids={parsed["voice_id"]}&source=phone', json=parsed['fields'])
        assert saved.status_code == 201
        assert saved.json()['record']['is_qualified'] == 1
        assert saved.json()['voice_ids'] == [parsed['voice_id']]
    assert client.get(f'/api/contracts/{contract}').json()['status'] == '合格'
    result = client.post(f'/api/contracts/{contract}/export').json()
    from app.storage import get_storage
    wb = openpyxl.load_workbook(BytesIO(get_storage().read(result['rel'])))
    try:
        assert wb.sheetnames == ['检验汇总']
        ws=wb.active
        assert [c.value for c in ws[16]][:3] == ['绝缘电阻(MΩ)', '控制线力矩(N·m)', '电气间隙(mm)']
        assert [c.value for c in ws[17]][:4] == [500,1.2,8,'合格']
        assert [c.value for c in ws[21]][:4] == ['98',99,1,'合格']
        assert ws['A21'].data_type == 's'
    finally:
        wb.close()
    bad = {'primer_thickness_ratio': '98', 'total_thickness_ratio': 99, 'adhesion_loss_ratio': 6}
    assert client.post(f'/api/contracts/{contract}/records/travel_paint', json=bad).json()['contract_status']['status'] == '不合格'
    assert client.post(f'/api/contracts/{contract}/export').status_code == 409
    assert client.post(f'/api/contracts/{contract}/records/travel_paint', json={**bad, 'adhesion_loss_ratio': 1}).json()['contract_status']['status'] == '合格'
    assert len(client.get(f'/api/contracts/{contract}/records/travel_paint').json()['items']) == 3
    timeline = client.get(f'/api/contracts/{contract}/timeline').json()['items']
    assert {'control_cabinet', 'travel_paint'} <= {x['kind'] for x in timeline if x['type'] == 'record' and x['voice_ids']}
    assert client.delete(f'/api/contracts/{contract}').status_code == 409


@pytest.mark.parametrize('form,text,expected', [
    ('travel_drive', '噪音82dB', {'noise': '82'}),
    ('control_cabinet', '绝缘电阻500兆欧', {'insulation_resistance': '500'}),
    ('travel_paint', '底漆厚度约八十微米', {'primer_thickness_ratio': '80'}),
])
def test_audio_api_accepts_new_forms_and_synonyms(client, monkeypatch, tmp_path, form, text, expected):
    monkeypatch.setenv('QC_AUDIO_DIR', str(tmp_path / 'audio'))
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'text': text}))
    monkeypatch.setattr(voice.httpx, 'Client', lambda **kwargs: original(transport=transport, **kwargs))
    response = client.post('/api/voice/parse', files={'audio': ('sample.wav', b'audio', 'audio/wav')}, data={'form': form})
    assert response.status_code == 200
    assert response.json()['fields'] == expected
    log = client.get(f'/api/voice/logs/{response.json()["voice_id"]}').json()
    assert log['status'] == 'ok' and log['has_audio']
