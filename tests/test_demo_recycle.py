"""演示合同回收、证据保护和跨日期重置的验收。"""

import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app import demo, events
from app.errors import Invalid
from app.models import Contract, DrumParam, TravelDriveParam, VoiceLog
from app.timeutil import today_bj


@pytest.fixture(autouse=True)
def recycle_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    publisher = events.EventBus()
    monkeypatch.setattr(demo, "bus", publisher)
    script = demo.load_script()
    script["steps"] = [{**step, "wait_ms": 0} for step in script["steps"]]
    monkeypatch.setattr(demo, "load_script", lambda: script)
    return publisher


def _code(number, day=None):
    return f"{(day or today_bj()):%Y%m%d}-{number:02d}"


def _contract(session, code, *, day=None, name=None, sources=("demo",), audio_path=None):
    session.add(Contract(ht_code=code, name=name if name is not None else demo.load_script()["name"],
                         date=day or today_bj()))
    for source in sources:
        session.add(VoiceLog(ht_code=code, form="drum", source=source, audio_path=audio_path,
                             text="旧演示语音", corrected="旧演示语音", result="{}", status="ok"))
    session.commit()


def _finish(client):
    client.app.state.demo._thread.join(timeout=5)
    assert not client.app.state.demo._thread.is_alive()
    assert client.get("/api/demo/status").json()["running"] is False


def _deleted(publisher):
    return [item for item in publisher.since(0, 1000)
            if item["type"] == "contract" and item["data"]["action"] == "deleted"]


def test_start_recycles_demo_and_removes_old_evidence(
        configured, session, monkeypatch, tmp_path, recycle_environment):
    first = configured.post("/api/demo/start", json={})
    assert first.status_code == 200
    code = first.json()["ht_code"]
    assert first.json()["recycled"] is None
    _finish(configured)
    session.expire_all()
    logs = session.scalars(select(VoiceLog).where(VoiceLog.ht_code == code)).all()
    assert len(logs) == 6
    audio = tmp_path / "audio" / "旧演示.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"demo")
    logs[0].audio_path = audio.name
    session.commit()
    archives = list((tmp_path / "exports").rglob("*.xlsx"))
    assert len(archives) == 1
    archive_content = archives[0].read_bytes()
    for number in range(20, 100, 10):
        _contract(session, _code(number), name="普通合同", sources=())
    monkeypatch.setattr(demo, "load_script", lambda: {
        "name": "演示客户（样例）", "steps": [{"say": "长度三千", "form": "drum"}]})

    restarted = configured.post("/api/demo/start", json={})
    assert restarted.status_code == 200
    assert restarted.json()["ht_code"] == restarted.json()["recycled"] == code
    _finish(configured)
    session.expire_all()
    assert session.scalars(select(Contract.ht_code).order_by(Contract.ht_code)).all() == [
        _code(number) for number in range(10, 100, 10)]
    assert session.scalars(select(DrumParam)).all() == []
    assert session.scalars(select(TravelDriveParam)).all() == []
    new_logs = session.scalars(select(VoiceLog).where(VoiceLog.ht_code == code)).all()
    assert len(new_logs) == 1 and new_logs[0].text == "长度三千"
    assert not audio.exists()
    assert archives[0].read_bytes() == archive_content
    contract_events = [item for item in recycle_environment.since(0, 1000)
                       if item["type"] == "contract"]
    assert [item["data"]["action"] for item in contract_events] == ["created", "deleted", "created"]
    assert contract_events[1]["data"] == {"action": "deleted", "contract": {"ht_code": code}}
    assert all(item["source"] == "demo" for item in contract_events)


def test_start_keeps_nine_ordinary_contracts(client, session, recycle_environment):
    for number in range(10, 100, 10):
        _contract(session, _code(number), name="普通合同", sources=())
    response = client.post("/api/demo/start", json={})
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "NO_CONTRACT_CODE", "message": "今天的合同号已用完"}}
    assert session.scalars(select(Contract.ht_code).order_by(Contract.ht_code)).all() == [
        _code(number) for number in range(10, 100, 10)]
    assert _deleted(recycle_environment) == []


@pytest.mark.parametrize("sources,name", [
    (("demo", "phone"), "演示客户（样例）"),
    (("demo", "screen"), "演示客户（样例）"),
    (("demo", None), "演示客户（样例）"),
    ((), "演示客户（样例）"),
    (("demo",), "普通合同"),
])
def test_non_demo_is_never_recycled_or_reset(
        client, session, tmp_path, recycle_environment, sources, name):
    code = _code(10)
    audio = tmp_path / "audio" / "真实录音.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"phone")
    _contract(session, code, sources=sources, name=name, audio_path=audio.name)
    session.add(DrumParam(ht_code=code, diameter=Decimal("1250"), is_qualified=1))
    session.commit()
    for number in range(20, 100, 10):
        _contract(session, _code(number), name="普通合同", sources=())
    assert demo.find_demo_contracts(session) == []
    with pytest.raises(Invalid) as caught:
        demo.purge_demo_contract(session, code)
    assert caught.value.code == "NOT_DEMO"
    assert caught.value.message == "不是演示合同，不能删除"
    assert client.post("/api/demo/start", json={}).json()["error"]["code"] == "NO_CONTRACT_CODE"
    assert client.post("/api/demo/reset").json() == {"deleted": []}
    session.expire_all()
    assert len(session.scalars(select(Contract)).all()) == 9
    assert len(session.scalars(select(VoiceLog)).all()) == len(sources)
    assert session.scalar(select(DrumParam.ht_code)) == code
    assert audio.exists()
    assert _deleted(recycle_environment) == []


def test_reset_all_dates_and_find_by_contract_date(client, session, recycle_environment):
    today = today_bj()
    yesterday = today - timedelta(days=1)
    older = today - timedelta(days=2)
    codes = [_code(20, yesterday), _code(10), _code(30)]
    _contract(session, codes[0], day=yesterday)
    _contract(session, codes[1])
    _contract(session, codes[2], day=older)
    protected = _code(40)
    _contract(session, protected, sources=("demo", "phone"))
    assert demo.find_demo_contracts(session) == sorted(codes)
    assert demo.find_demo_contracts(session, today) == [codes[1]]
    assert demo.find_demo_contracts(session, older) == [codes[2]]
    response = client.post("/api/demo/reset")
    assert response.status_code == 200 and response.json() == {"deleted": sorted(codes)}
    session.expire_all()
    assert session.scalars(select(Contract.ht_code)).all() == [protected]
    assert session.scalars(select(VoiceLog.ht_code)).all() == [protected, protected]
    deleted = _deleted(recycle_environment)
    assert [item["data"] for item in deleted] == [
        {"action": "deleted", "contract": {"ht_code": code}} for code in sorted(codes)]
    assert all(item["source"] == "demo" for item in deleted)
    assert client.post("/api/demo/reset").json() == {"deleted": []}


def test_running_reset_is_rejected(client, session, monkeypatch, recycle_environment):
    old_code = _code(10, today_bj() - timedelta(days=1))
    _contract(session, old_code, day=today_bj() - timedelta(days=1))
    entered = threading.Event()
    original = demo.run_script

    def observed(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(demo, "run_script", observed)
    monkeypatch.setattr(demo, "load_script", lambda: {"name": "演示客户（样例）", "steps": [
        {"focus": "drum", "wait_ms": 60000}]})
    assert client.post("/api/demo/start", json={}).status_code == 200
    assert entered.wait(2)
    response = client.post("/api/demo/reset")
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "DEMO_RUNNING", "message": "演示正在进行"}}
    assert session.scalar(select(Contract.ht_code).where(Contract.ht_code == old_code)) == old_code
    assert _deleted(recycle_environment) == []
    client.post("/api/demo/stop")
    assert client.post("/api/demo/reset").json() == {"deleted": [old_code]}


def test_start_recycles_smallest_today_only(client, session, monkeypatch, recycle_environment):
    yesterday = today_bj() - timedelta(days=1)
    old_code = _code(10, yesterday)
    _contract(session, old_code, day=yesterday)
    for number in range(10, 100, 10):
        _contract(session, _code(number), name="演示客户（样例）" if number in (30, 70) else "普通合同")
    monkeypatch.setattr(demo, "load_script", lambda: {"name": "演示客户（样例）", "steps": []})
    response = client.post("/api/demo/start", json={})
    assert response.status_code == 200
    assert response.json()["recycled"] == _code(30)
    _finish(client)
    session.expire_all()
    assert demo.find_demo_contracts(session) == [old_code, _code(70)]


def test_yesterday_demo_cannot_free_today_number(client, session, recycle_environment):
    yesterday = today_bj() - timedelta(days=1)
    code = _code(10, yesterday)
    _contract(session, code, day=yesterday)
    for number in range(10, 100, 10):
        _contract(session, _code(number), name="普通合同", sources=())
    response = client.post("/api/demo/start", json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "NO_CONTRACT_CODE"
    assert demo.find_demo_contracts(session) == [code]
    assert _deleted(recycle_environment) == []


def test_purge_counts_and_logs_audio_failure(session, monkeypatch, tmp_path, caplog):
    code = _code(10)
    audio = tmp_path / "audio" / "无法删除.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"demo")
    _contract(session, code, sources=("demo", "demo"), audio_path=audio.name)
    session.add_all([DrumParam(ht_code=code, is_qualified=1), DrumParam(ht_code=code, is_qualified=0),
                     TravelDriveParam(ht_code=code, is_qualified=None)])
    session.commit()
    original = Path.unlink

    def fail_audio(path, *args, **kwargs):
        if path == audio:
            raise PermissionError("录音文件不可删除")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_audio)
    assert demo.purge_demo_contract(session, code) == {"ht_code": code, "records": 3, "voice_logs": 2}
    assert session.scalars(select(Contract)).all() == []
    assert session.scalars(select(VoiceLog)).all() == []
    assert session.scalars(select(DrumParam)).all() == []
    assert session.scalars(select(TravelDriveParam)).all() == []
    assert "删除演示录音失败" in caplog.text
    assert audio.exists()


def test_purge_database_failure_rolls_back_before_audio_removal(session, tmp_path):
    code = _code(10)
    audio = tmp_path / "audio" / "应当保留.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"demo")
    _contract(session, code, audio_path=audio.name)
    session.add_all([DrumParam(ht_code=code, is_qualified=1), TravelDriveParam(ht_code=code)])
    session.commit()
    engine = session.get_bind()

    def fail_contract_delete(connection, cursor, statement, parameters, context, many):
        if statement.startswith("DELETE FROM contract "):
            raise RuntimeError("模拟合同删除失败")

    event.listen(engine, "before_cursor_execute", fail_contract_delete)
    try:
        with pytest.raises(RuntimeError, match="模拟合同删除失败"):
            demo.purge_demo_contract(session, code)
    finally:
        event.remove(engine, "before_cursor_execute", fail_contract_delete)
    assert session.scalar(select(Contract.ht_code)) == code
    assert session.scalar(select(VoiceLog.ht_code)) == code
    assert session.scalar(select(DrumParam.ht_code)) == code
    assert session.scalar(select(TravelDriveParam.ht_code)) == code
    assert audio.exists()
