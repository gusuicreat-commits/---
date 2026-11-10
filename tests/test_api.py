from datetime import date, datetime
from zoneinfo import ZoneInfo
import time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import timeutil
from app.config import ROOT, get_settings
from app.db import get_session, init_db
from app.fields import load_fields
from app.models import Contract, DrumParam, Rule
from app.seed_demo import main as seed_demo
from app.services import rules


def assert_error(response, status, code, message=None):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str)
    if message:
        assert error["message"] == message
    return error


def test_health_fields(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/fields").json() == load_fields()
    for path in ["/api/health", "/api/fields"]:
        assert_error(client.post(path), 405, "BAD_REQUEST")


def test_contract_crud_and_search(client):
    response = client.post("/api/contracts", json={"ht_code": " ２０２６１１１０－１０ ", "name": "  客户甲  ", "date": "2020-01-02"})
    assert response.status_code == 201
    item = response.json()
    assert item == {"id": item["id"], "ht_code": "20261110-10", "name": "客户甲", "date": "2020-01-02"}
    for q in ["61110", "客户甲", "2020-01"]:
        items = client.get("/api/contracts", params={"q": q, "status": "未录全"}).json()["items"]
        assert len(items) == 1
        assert items[0]["status_reason"] == "缺少卷筒参数和大车传动参数和控制柜参数和大车油漆参数"
    assert client.get("/api/contracts", params={"q": "%"}).json()["items"] == []
    assert client.get("/api/contracts", params={"status": "合格"}).json()["items"] == []
    assert_error(client.get("/api/contracts?status=未知"), 422, "BAD_REQUEST")
    response = client.patch("/api/contracts/２０２６１１１０－１０", json={"name": "  客户乙 ", "date": "2026-11-11", "ht_code": " 20261110-10 "})
    assert response.status_code == 200
    assert response.json()["name"] == "客户乙"
    detail = client.get("/api/contracts/ ２０２６１１１０－１０ ").json()
    assert detail["contract"]["date"] == "2026-11-11"
    assert detail["latest"] == {"drum": None, "travel_drive": None, "control_cabinet": None, "travel_paint": None}
    assert detail["rules"] == {"configured": 0, "total": 11}
    response = client.delete("/api/contracts/２０２６１１１０－１０")
    assert response.status_code == 204 and response.content == b""
    for method in ["get", "patch", "delete"]:
        response = getattr(client, method)("/api/contracts/20261110-10", **({"json": {}} if method == "patch" else {}))
        assert_error(response, 404, "NOT_FOUND", "合同不存在，请先新建合同")


@pytest.mark.parametrize("body,code,message", [
    ({"ht_code": "错误", "name": "客户"}, "FORMAT", "合同号格式应为「4位年份＋4位数字」，例如 20264827"),
    ({"ht_code": "20261110-10", "name": " "}, "NAME_EMPTY", "客户名称不能为空"),
    ({"ht_code": "20261110-10", "name": "字" * 129}, "NAME_TOO_LONG", "客户名称最多 128 个字"),
])
def test_create_invalid(client, body, code, message):
    assert_error(client.post("/api/contracts", json=body), 422, code, message)


def test_duplicate_and_bj_suggestion(client, monkeypatch):
    fixed = datetime(2026, 11, 11, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    try:
        with monkeypatch.context() as clock_patch:
            clock_patch.setenv("TZ", "UTC")
            if hasattr(time, "tzset"):
                time.tzset()
            clock_patch.setattr(timeutil, "now_bj", lambda: fixed)
            assert fixed.astimezone(ZoneInfo("UTC")).date() == date(2026, 11, 10)
            monkeypatch.setattr("app.htcode.secrets.choice", lambda pool: pool[0])
            assert client.get("/api/contracts/suggest").json() == {"ht_code": "20260000"}
            body = {"ht_code": "20260000", "name": "客户"}
            assert client.post("/api/contracts", json=body).json()["date"] == "2026-11-11"
            error = assert_error(client.post("/api/contracts", json=body), 409, "DUPLICATE", "合同号已存在")
            assert error["suggestion"] == "20260001"
            assert client.post("/api/contracts", json={**body, "ht_code": "20260001"}).status_code == 201
            assert client.get("/api/contracts/suggest").json() == {"ht_code": "20260002"}
    finally:
        if hasattr(time, "tzset"):
            time.tzset()


@pytest.mark.parametrize("path,body", [
    ("/api/contracts", {"ht_code": "20261110-10"}),
    ("/api/contracts", {"ht_code": "20261110-10", "name": "客户", "date": "不是日期"}),
])
def test_bad_body(client, path, body):
    assert client.post(path, json=body).json() == {"error": {"code": "BAD_REQUEST", "message": "请求格式不正确"}}
    assert_error(client.post(path, content="{", headers={"Content-Type": "application/json"}), 422, "BAD_REQUEST")


def test_record_api(client, contract, good_drum, complete_new_parameters):
    complete_new_parameters(contract)
    response = client.post(f"/api/contracts/ ２０２６１１１０－１０ /records/drum", json={**good_drum, "diameter": 1260.0, "unknown": "忽略", "is_qualified": 0})
    assert response.status_code == 201
    result = response.json()
    assert result["record"]["diameter"] == "1260"
    assert result["record"]["is_qualified"] == 1
    assert "unknown" not in result["record"]
    assert result["warning"] is None
    assert len(result["record"]["created_at"]) == 19
    assert len(result["validation"]["items"]) == 4
    response = client.post(f"/api/contracts/{contract}/records/travel_drive", json={"noise": "８０.５０", "motor_nameplate": " 铭牌 ", "reducer_nameplate": " "})
    assert response.status_code == 201
    result = response.json()
    assert result["record"]["noise"] == "80.5"
    assert result["record"]["motor_nameplate"] == "铭牌"
    assert result["record"]["reducer_nameplate"] is None
    assert client.get(f"/api/contracts/{contract}/records/drum").json()["items"][0]["diameter"] == "1260"
    assert client.get("/api/contracts?status=合格").json()["items"][0]["ht_code"] == contract
    for method in ["get", "post"]:
        kwargs = {"json": {}} if method == "post" else {}
        assert_error(getattr(client, method)(f"/api/contracts/{contract}/records/unknown", **kwargs), 404, "NOT_FOUND", "参数类别不存在")
        assert_error(getattr(client, method)("/api/contracts/20261110-90/records/drum", **kwargs), 404, "NOT_FOUND", "合同不存在，请先新建合同")


@pytest.mark.parametrize("kind,payload,field,message", [
    ("drum", {"diameter": "abc"}, "diameter", "直径必须是数字"),
    ("drum", {"length": "12.345"}, "length", "长度最多两位小数"),
    ("travel_drive", {"noise": "nan"}, "noise", "噪声必须是数字"),
    ("travel_drive", {"motor_nameplate": "字" * 256}, "motor_nameplate", "电机铭牌最多 255 个字"),
])
def test_field_errors(client, contract, session, kind, payload, field, message):
    response = client.post(f"/api/contracts/{contract}/records/{kind}", json=payload)
    assert assert_error(response, 422, "FIELD_INVALID")["fields"][field] == message
    assert session.scalars(select(DrumParam)).all() == []


def test_rules_api(client, session):
    data = client.get("/api/rules").json()
    assert data["configured"] == 0 and data["total"] == 11
    assert [item["name"] for item in data["items"]] == [field["name"] for meta in load_fields().values() for field in meta["fields"] if field["validate"]]
    rule_id = data["items"][0]["id"]
    path = f"/api/rules/{rule_id}"
    assert client.put(path, json={"min": "1240", "max": "1260"}).json()["configured"] is True
    assert client.put(path, json={"min": None, "max": "1260"}).json()["configured"] is False
    assert client.get("/api/rules").json()["configured"] == 0
    for body, code, message in [
        ({"min": "abc", "max": 1}, "FIELD_INVALID", "下限必须是数字"),
        ({"min": 1, "max": "abc"}, "FIELD_INVALID", "上限必须是数字"),
        ({"min": "1.001", "max": 2}, "FIELD_INVALID", "最多两位小数"),
        ({"min": 2, "max": "2.001"}, "FIELD_INVALID", "最多两位小数"),
        ({"min": 3, "max": 2}, "RULE_MIN_GT_MAX", "下限不能大于上限"),
    ]:
        assert_error(client.put(path, json=body), 422, code, message)
    assert_error(client.put("/api/rules/999", json={"min": 1, "max": 2}), 404, "NOT_FOUND")
    assert_error(client.put(path, json={"min": 1}), 422, "BAD_REQUEST")
    unknown = Rule(table_name="travel_drive_param", name="motor_nameplate")
    session.add(unknown)
    session.commit()
    assert_error(client.put(f"/api/rules/{unknown.id}", json={"min": 1, "max": 2}), 422, "RULE_FIELD_UNKNOWN", "该参数不在校验字段清单中")


def test_revalidate_missing_and_db_error(client):
    assert_error(client.post("/api/contracts/20261110-90/revalidate"), 404, "NOT_FOUND")
    assert client.post("/api/validation/pending").json() == {"processed": 0, "qualified": 0, "unqualified": 0, "failed": 0}

    def unavailable_session():
        raise OperationalError("模拟数据库故障", {}, Exception("数据库不可用"))
        yield

    client.app.dependency_overrides[get_session] = unavailable_session
    try:
        for method, path in [("get", "/api/contracts/suggest"), ("get", "/api/rules"),
                             ("post", "/api/validation/pending")]:
            response = getattr(client, method)(path)
            assert response.json() == {"error": {"code": "DB_UNAVAILABLE", "message": "系统暂时不可用"}}
            assert response.status_code == 503
    finally:
        client.app.dependency_overrides.clear()


def test_init_db_idempotent(session):
    rule = session.scalar(select(Rule).where(Rule.name == "diameter"))
    rules.update_rule(session, rule.id, 1, 2)
    missing = session.scalar(select(Rule).where(Rule.name == "noise"))
    session.delete(missing)
    session.commit()
    init_db()
    init_db()
    session.expire_all()
    assert len(session.scalars(select(Rule)).all()) == 11
    assert session.get(Rule, rule.id).min == 1
    assert session.scalar(select(Rule).where(Rule.name == "noise")).min is None


def test_config_and_database_isolation(client, tmp_path, monkeypatch):
    client.post("/api/contracts", json={"ht_code": "20261110-10", "name": "客户"})
    monkeypatch.setenv("QC_DB_PATH", str(tmp_path / "other" / "qc.sqlite3"))
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as other:
        assert other.get("/api/contracts").json()["items"] == []
        assert len(client.get("/api/contracts").json()["items"]) == 1
    monkeypatch.setenv("QC_DB_PATH", "data/relative.sqlite3")
    monkeypatch.setenv("QC_EXPORT_DIR", "custom_exports")
    monkeypatch.setenv("QC_WEB_DIR", "custom_web")
    monkeypatch.setenv("QC_ASR_URL", "http://localhost:9999")
    assert get_settings().db_path == ROOT / "data/relative.sqlite3"
    assert get_settings().export_dir == ROOT / "custom_exports"
    assert get_settings().web_dir == ROOT / "custom_web"
    assert get_settings().asr_url == "http://localhost:9999"


def test_static_mounted_after_api(client, tmp_path, monkeypatch):
    web = tmp_path / "页面"
    web.mkdir()
    (web / "index.html").write_text("<html>测试页面</html>", encoding="utf-8")
    monkeypatch.setenv("QC_WEB_DIR", str(web))
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as static_client:
        assert "测试页面" in static_client.get("/").text
        assert static_client.get("/api/health").json() == {"ok": True}
        assert static_client.get("/api/rules").json()["total"] == 11


def test_seed_demo_idempotent(client, session, capsys):
    seed_demo()
    seed_demo()
    assert "写入的是样例数据" in capsys.readouterr().out
    assert len(session.scalars(select(Contract)).all()) == 3
    states = {item["name"]: item["status"] for item in client.get("/api/contracts").json()["items"]}
    assert states == {"样例客户甲": "合格", "样例客户乙": "不合格", "样例客户丙": "未录全"}
    assert len(session.scalars(select(DrumParam)).all()) == 2


def test_seed_preserves_partially_configured_rules(client, session):
    rule = session.scalar(select(Rule).where(Rule.name == "diameter"))
    rules.update_rule(session, rule.id, 1200, None)
    seed_demo()
    session.expire_all()
    assert session.get(Rule, rule.id).min == 1200
    assert session.get(Rule, rule.id).max is None
    assert session.scalar(select(Rule).where(Rule.name == "noise")).min is None
