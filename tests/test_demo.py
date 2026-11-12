"""演示使用真实解析、校验与导出的端到端验收。"""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import demo, events
from app.models import Contract, VoiceLog
from app.services import voicelog
from app.services.status import contract_status


@pytest.fixture(autouse=True)
def demo_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    current = events.EventBus()
    monkeypatch.setattr(demo, "bus", current)
    monkeypatch.setattr(voicelog, "bus", current)
    return current


def test_script_real_workflow(configured, session, tmp_path, demo_environment):
    contract = configured.post("/api/contracts", json={
        "ht_code": "20261110-10", "name": "演示客户（样例）"}).json()
    script = demo.load_script()
    steps = [{**step, "wait_ms": 0} for step in script["steps"]]
    factory = sessionmaker(bind=session.get_bind())
    demo.run_script(factory, steps, contract["ht_code"], "同步验收")
    session.expire_all()
    assert contract_status(session, contract["ht_code"])["status"] == "合格"
    assert len(list((tmp_path / "exports").rglob("*.xlsx"))) == 1
    reviews = session.scalars(select(VoiceLog).where(VoiceLog.status == "review")).all()
    assert len(reviews) == 1 and reviews[0].text == "厚度恁等一下，俺再量量"
    all_events = demo_environment.since(0, 1000)
    progress = [item["data"] for item in all_events if item["type"] == "demo"]
    assert [item["index"] for item in progress if item["state"] == "step"] == list(range(len(steps)))
    assert progress[-1] == {"state": "done", "run_id": "同步验收", "ht_code": contract["ht_code"]}
    assert all(item["source"] == "demo" for item in all_events)
    submissions = [item["data"] for item in all_events if item["type"] == "record"]
    assert [item["record"]["is_qualified"] for item in submissions] == [1, 0, 1, 1, 1]
    assert [len(item["voice_ids"]) for item in submissions] == [4, 1, 2, 1, 1]
    assert submissions[2]["record"]["reducer_nameplate"] == "ZQ500样例"
    assert submissions[2]["record"]["motor_nameplate"] == "YZR160M2-6样例"
    assert any(item["type"] == "export" for item in all_events)
    drafts = [item["data"] for item in all_events if item["type"] == "draft"]
    assert drafts[0]["fields"]["diameter"] == "1262"
    assert drafts[1]["fields"]["diameter"] == "1252"


def test_demo_start_stop_status(client, session, monkeypatch, demo_environment):
    entered = threading.Event()
    original = demo.run_script

    def observed(*args, **kwargs):
        progress = kwargs["on_step"]

        def on_step(index):
            progress(index)
            entered.set()

        kwargs["on_step"] = on_step
        return original(*args, **kwargs)

    monkeypatch.setattr(demo, "run_script", observed)
    monkeypatch.setattr(demo, "load_script", lambda: {"name": "演示客户（样例）", "steps": [
        {"focus": "drum", "wait_ms": 60000, "caption": "等待停止"}]})
    started = client.post("/api/demo/start", json={"speed": 1})
    assert started.status_code == 200
    assert entered.wait(2)
    info = started.json()
    assert info["total"] == 1
    contract = session.scalar(select(Contract).where(Contract.ht_code == info["ht_code"]))
    assert contract.name == "演示客户（样例）"
    conflict = client.post("/api/demo/start", json={})
    assert conflict.status_code == 409 and conflict.json()["error"] == {
        "code": "DEMO_RUNNING", "message": "演示正在进行"}
    assert client.get("/api/demo/status").json()["running"] is True
    assert client.post("/api/demo/stop").json() == {"running": False}
    status = client.get("/api/demo/status").json()
    assert status == {"running": False, **info, "index": 0}
    assert client.post("/api/demo/stop").json() == {"running": False}
    assert demo_environment.since(0)[-1]["data"]["state"] == "stopped"
    restarted = client.post("/api/demo/start", json={"speed": 4})
    assert restarted.status_code == 200 and restarted.json()["ht_code"] != info["ht_code"]
    client.post("/api/demo/stop")


@pytest.mark.parametrize("speed", [0, 0.24, 4.01])
def test_demo_speed_bounds(client, speed):
    assert client.post("/api/demo/start", json={"speed": speed}).status_code == 422
    assert client.get("/api/demo/status").json()["running"] is False


def test_export_denied_continues_and_unknown_step_errors(client, session, demo_environment):
    code = "20261110-10"
    client.post("/api/contracts", json={"ht_code": code, "name": "未录全"})
    factory = sessionmaker(bind=session.get_bind())
    demo.run_script(factory, [{"export": True}, {"focus": "drum"}], code)
    progress = [item["data"]["state"] for item in demo_environment.since(0)
                if item["type"] == "demo"]
    assert progress == ["step", "export_failed", "step", "done"]
    demo.run_script(factory, [{"bad": True}], code)
    assert demo_environment.since(0)[-1]["data"]["state"] == "error"
    assert demo_environment.since(0)[-1]["data"]["message"] == "演示步骤类型不正确"


def test_draft_clears_on_qualified_and_fills_latest(configured, session, demo_environment):
    code = "20261110-10"
    configured.post("/api/contracts", json={"ht_code": code, "name": "演示"})
    steps = [
        {"say": "直径一千二百五十，长度三千，厚度二十，光段三百", "form": "drum"},
        {"submit": "drum"}, {"say": "直径一千二百五十二", "form": "drum"},
        {"submit": "drum"}]
    demo.run_script(sessionmaker(bind=session.get_bind()), steps, code)
    items = demo_environment.since(0)
    drafts = [item["data"]["fields"] for item in items if item["type"] == "draft"]
    assert drafts[-1] == {"diameter": "1252"}
    records = [item["data"] for item in items if item["type"] == "record"]
    assert records[-1]["record"]["length"] == "3000"
    assert records[-1]["record"]["is_qualified"] == 1
    assert len(records[-1]["voice_ids"]) == 1
    assert records[0]["voice_ids"] != records[-1]["voice_ids"]
