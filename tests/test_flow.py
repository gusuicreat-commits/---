import pytest
from sqlalchemy import select

from app.models import DrumParam, Rule
from app.services import validation


def post_record(client, code, kind, values):
    response = client.post(f"/api/contracts/{code}/records/{kind}", json=values)
    assert response.status_code == 201, response.text
    return response.json()


def fail_validation(*args, **kwargs):
    raise RuntimeError("模拟校验故障")


@pytest.mark.parametrize("first,second,expected", [(1262, 1250, "合格"), (1250, 1262, "不合格")])
def test_latest_wins(client, contract, good_drum, first, second, expected, complete_new_parameters):
    complete_new_parameters(contract)
    post_record(client, contract, "travel_drive", {"noise": 80})
    post_record(client, contract, "drum", {**good_drum, "diameter": first})
    result = post_record(client, contract, "drum", {**good_drum, "diameter": second})
    assert result["contract_status"]["status"] == expected
    detail = client.get(f"/api/contracts/{contract}").json()
    history = client.get(f"/api/contracts/{contract}/records/drum").json()["items"]
    assert detail["status"] == expected
    assert detail["latest"]["drum"] == history[0]
    assert history[0]["id"] > history[1]["id"]


def test_saved_on_validation_failure(client, contract, good_drum, monkeypatch, session, complete_new_parameters):
    complete_new_parameters(contract)
    post_record(client, contract, "travel_drive", {"noise": 80})
    original = validation.validate_record
    monkeypatch.setattr(validation, "validate_record", fail_validation)
    result = post_record(client, contract, "drum", good_drum)
    assert result["record"]["is_qualified"] is None
    assert result["validation"] is None
    assert result["warning"] == "已保存，校验未完成，可稍后补跑校验"
    assert result["contract_status"] == {"status": "待校验", "reason": "有记录尚未校验"}
    assert session.get(DrumParam, result["record"]["id"]).is_qualified is None
    monkeypatch.setattr(validation, "validate_record", original)
    assert client.post("/api/validation/pending").json() == {
        "processed": 1, "qualified": 1, "unqualified": 0, "failed": 0}
    assert client.get(f"/api/contracts/{contract}").json()["status"] == "合格"
    assert client.post("/api/validation/pending").json()["processed"] == 0


def test_rules_do_not_rejudge_until_requested(client, contract, good_drum, complete_new_parameters):
    complete_new_parameters(contract)
    post_record(client, contract, "drum", good_drum)
    post_record(client, contract, "drum", good_drum)
    post_record(client, contract, "travel_drive", {"noise": 80})
    rule = next(item for item in client.get("/api/rules").json()["items"] if item["name"] == "diameter")
    assert client.put(f"/api/rules/{rule['id']}", json={"min": 1251, "max": 1260}).status_code == 200
    assert client.get(f"/api/contracts/{contract}").json()["status"] == "合格"
    assert client.post("/api/validation/pending").json()["processed"] == 0
    result = client.post(f"/api/contracts/{contract}/revalidate").json()
    assert result["records"] == 5
    assert result["contract_status"]["status"] == "不合格"
    history = client.get(f"/api/contracts/{contract}/records/drum").json()["items"]
    assert all(item["is_qualified"] == 0 for item in history)


def test_delete_and_code_locked(client, contract, good_drum):
    post_record(client, contract, "drum", good_drum)
    response = client.delete(f"/api/contracts/{contract}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HAS_RECORDS"
    response = client.patch(f"/api/contracts/{contract}", json={"ht_code": "20261110-20"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "HT_CODE_LOCKED"


def test_unqualified_beats_pending(client, contract, good_drum, monkeypatch, complete_new_parameters):
    complete_new_parameters(contract)
    post_record(client, contract, "drum", {**good_drum, "diameter": 1262})
    monkeypatch.setattr(validation, "validate_record", fail_validation)
    result = post_record(client, contract, "travel_drive", {"noise": 80})
    assert result["contract_status"] == {"status": "不合格", "reason": "卷筒参数不合格"}


def test_incomplete_and_both_failures(client, contract, good_drum, complete_new_parameters):
    assert client.get(f"/api/contracts/{contract}").json()["status_reason"] == "缺少卷筒参数和大车传动参数和控制柜参数和大车油漆参数"
    complete_new_parameters(contract)
    result = post_record(client, contract, "drum", {**good_drum, "diameter": 1262})
    assert result["contract_status"] == {"status": "未录全", "reason": "缺少大车传动参数"}
    result = post_record(client, contract, "travel_drive", {"noise": 86})
    assert result["contract_status"]["reason"] == "卷筒和大车传动参数都不合格"
    result = post_record(client, contract, "drum", good_drum)
    assert result["contract_status"]["reason"] == "大车传动参数不合格"


def test_pending_continues_after_single_failure(client, contract, good_drum, monkeypatch):
    original = validation.validate_record
    monkeypatch.setattr(validation, "validate_record", fail_validation)
    first = post_record(client, contract, "drum", good_drum)["record"]["id"]
    post_record(client, contract, "drum", {**good_drum, "diameter": 1262})
    post_record(client, contract, "travel_drive", {"noise": 80})

    def fail_first(session, table, record):
        if table == "drum_param" and record.id == first:
            record.is_qualified = 1
            session.flush()
            raise RuntimeError("模拟单条校验失败")
        return original(session, table, record)

    monkeypatch.setattr(validation, "validate_record", fail_first)
    assert client.post("/api/validation/pending").json() == {
        "processed": 2, "qualified": 1, "unqualified": 1, "failed": 1}
    assert client.get(f"/api/contracts/{contract}/records/drum").json()["items"][1]["is_qualified"] is None


def test_same_session_reads_fresh_rules(session, configured):
    rule = session.scalar(select(Rule).where(Rule.name == "diameter"))
    record = DrumParam(ht_code="20261110-10", diameter=1250, length=3000, thick=20, light=310)
    session.add(record)
    session.commit()
    assert validation.validate_record(session, "drum_param", record)["is_qualified"] == 1
    configured.put(f"/api/rules/{rule.id}", json={"min": 1251, "max": 1260})
    assert validation.validate_record(session, "drum_param", record)["is_qualified"] == 0
