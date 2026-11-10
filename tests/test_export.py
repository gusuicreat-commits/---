from datetime import date, datetime
from contextlib import contextmanager
from decimal import Decimal
from io import BytesIO
import os

import openpyxl
import pytest
from sqlalchemy import select

from app.dec import to_dec2
from app.errors import AppError, TooManyDecimals
from app.export import registry, sheet_drum, sheet_travel
from app.models import DrumParam
from app.services import export
from app.storage import LocalStorage, get_storage


@pytest.fixture(autouse=True)
def storage_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "backups"))


def submit(client, code, kind, values):
    response = client.post(f"/api/contracts/{code}/records/{kind}", json=values)
    assert response.status_code == 201
    return response.json()


def qualify(client, code, good_drum):
    submit(client, code, "drum", good_drum)
    submit(client, code, "travel_drive", {
        "reducer_nameplate": "ZQ500", "motor_nameplate": "YZR160", "noise": "80"})


@contextmanager
def workbook(result):
    wb = openpyxl.load_workbook(BytesIO(get_storage().read(result["rel"])))
    try:
        yield wb
    finally:
        wb.close()


def test_qualified_export_uses_latest_and_formats(client, contract, good_drum):
    submit(client, contract, "drum", {**good_drum, "diameter": "999"})
    qualify(client, contract, good_drum)
    response = client.post(f"/api/contracts/{contract}/export")
    assert response.status_code == 200
    result = response.json()
    assert all(item["ok"] and item["error"] is None for item in result["sheets"])
    with workbook(result) as wb:
        assert wb.sheetnames == ["合同信息", "卷筒参数", "大车传动参数"]
        contract_ws, drum, travel = wb.worksheets
        assert [cell.value for cell in contract_ws[1]] == ["合同号", "客户名称", "录入日期"]
        assert contract_ws["A2"].value == contract
        assert contract_ws["A2"].data_type == "s"
        assert contract_ws["A2"].number_format == "@"
        assert isinstance(contract_ws["C2"].value, date)
        assert contract_ws["C2"].number_format == "yyyy-mm-dd"
        assert [cell.value for cell in drum[1]] == [
            "直径(mm)", "长度(mm)", "厚度(mm)", "光段(mm)", "是否合格", "录入时间"]
        assert [cell.value for cell in drum[2]][:5] == [1250, 3000, 20, 310, "合格"]
        assert drum["A2"].number_format == "0.00"
        datetime.strptime(drum["F2"].value, "%Y-%m-%d %H:%M:%S")
        assert [cell.value for cell in travel[1]] == [
            "减速机铭牌", "电机铭牌", "噪声(dB)", "是否合格", "录入时间"]
        assert [cell.value for cell in travel[2]][:4] == ["ZQ500", "YZR160", 80, "合格"]
        for ws in wb:
            assert ws["A1"].font.bold
            assert ws["A1"].fill.fgColor.rgb == "00EEEEEE"
            assert all(dimension.width >= 12 for dimension in ws.column_dimensions.values())


@pytest.mark.parametrize("state", ["不合格", "未录全", "待校验"])
def test_unqualified_export_rejected(client, session, contract, good_drum, state):
    if state != "未录全":
        qualify(client, contract, good_drum)
        if state == "不合格":
            submit(client, contract, "drum", {**good_drum, "diameter": "1"})
        else:
            record = session.scalar(select(DrumParam).where(DrumParam.ht_code == contract))
            record.is_qualified = None
            session.commit()
    current = client.get(f"/api/contracts/{contract}").json()
    response = client.post(f"/api/contracts/{contract}/export")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "NOT_QUALIFIED"
    assert error["status"] == state
    assert state in error["message"]
    assert current["status_reason"] in error["message"]
    assert get_storage().list() == []


def test_missing_contract(client):
    response = client.post("/api/contracts/20261110-10/export")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "合同不存在，请先新建合同"


def test_sheet_failure_isolated(client, contract, good_drum, monkeypatch):
    qualify(client, contract, good_drum)

    def fail(ws, session, ht_code):
        ws["Z9"] = "残留数据"
        ws.merge_cells("A1:C1")
        raise RuntimeError("模拟生成故障")

    monkeypatch.setitem(registry.GENERATORS, "drum", fail)
    response = client.post(f"/api/contracts/{contract}/export")
    assert response.status_code == 200
    result = response.json()
    assert result["sheets"][1] == {
        "name": "卷筒参数", "ok": False, "error": "RuntimeError: 模拟生成故障"}
    with workbook(result) as wb:
        assert wb["合同信息"]["A2"].value == contract
        assert wb["大车传动参数"]["C2"].value == 80
        assert wb["卷筒参数"]["A1"].value == "生成失败"
        assert wb["卷筒参数"]["A2"].value == "原因：RuntimeError: 模拟生成故障"
        assert wb["卷筒参数"].max_row == 2
        assert wb["卷筒参数"].max_column == 1


def test_same_day_overwrite_and_filename(client, contract, good_drum, monkeypatch):
    monkeypatch.setattr(export, "today_bj", lambda: date(2027, 1, 2))
    qualify(client, contract, good_drum)
    first = client.post(f"/api/contracts/{contract}/export").json()
    submit(client, contract, "drum", {**good_drum, "diameter": "1251"})
    second = client.post(f"/api/contracts/{contract}/export").json()
    assert first["rel"] == second["rel"] == f"2027-01/{contract}_20270102.xlsx"
    assert second["file"] == f"{contract}_20270102.xlsx"
    assert len(get_storage().list()) == 1
    with workbook(second) as wb:
        assert wb["卷筒参数"]["A2"].value == 1251
    items = client.get("/api/exports").json()["items"]
    assert len(items) == 1
    assert items[0]["ht_code"] == contract
    assert items[0]["date"] == "2027-01-02"


def test_export_all_continues_after_failure(client, configured, good_drum, monkeypatch):
    codes = ["20261110-10", "20261110-20", "20261110-30"]
    for code in codes:
        assert client.post("/api/contracts", json={"ht_code": code, "name": "客户"}).status_code == 201
        qualify(client, code, good_drum)
    submit(client, codes[2], "drum", {**good_drum, "thick": "100"})
    original = export.export_one

    def fail_one(session, ht_code):
        if ht_code == codes[0]:
            raise RuntimeError("模拟单合同故障")
        return original(session, ht_code)

    monkeypatch.setattr(export, "export_one", fail_one)
    response = client.post("/api/export/all")
    assert response.status_code == 200
    result = response.json()
    assert len(result["succeeded"]) == 1
    assert result["succeeded"][0]["ht_code"] == codes[1]
    assert result["failed"] == [{"ht_code": codes[0], "reason": "模拟单合同故障"}]
    assert result["skipped"] == 1


def test_download_and_path_rejection(client, contract, good_drum):
    qualify(client, contract, good_drum)
    result = client.post(f"/api/contracts/{contract}/export").json()
    day = result["file"].rsplit("_", 1)[1].removesuffix(".xlsx")
    response = client.get(f"/api/exports/{contract}/{day}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    wb = openpyxl.load_workbook(BytesIO(response.content))
    assert wb["合同信息"]["A2"].value == contract
    wb.close()
    missing = client.get(f"/api/exports/{contract}/20000101")
    assert missing.status_code == 404
    assert missing.json()["error"] == {"code": "FILE_NOT_FOUND", "message": "文件不存在，请先导出"}
    for code in ["..", "%2E%2E", "..%2Fsecret", "20261110-10.."]:
        assert client.get(f"/api/exports/{code}/{day}").status_code in {400, 404}


def test_local_storage_sandbox_and_listing(tmp_path):
    storage = LocalStorage(tmp_path / "exports")
    assert storage.save("2026-11/旧.xlsx", "旧内容".encode()) == "2026-11/旧.xlsx"
    storage.save("2026-11/新.xlsx", "新内容".encode())
    storage.save("备注.txt", "备注".encode())
    os.utime(storage.path("2026-11/旧.xlsx"), (0, 0))
    items = storage.list()
    assert [item["rel"] for item in items] == ["2026-11/新.xlsx", "2026-11/旧.xlsx"]
    assert items[1]["mtime"] == "1970-01-01 08:00:00"
    assert storage.read(items[0]["rel"]) == "新内容".encode()
    assert storage.exists(items[0]["rel"])
    assert not storage.exists("不存在.xlsx")
    (tmp_path / "外部.xlsx").write_bytes("外部".encode())
    storage.path("链接.xlsx").symlink_to(tmp_path / "外部.xlsx")
    for rel in ["../外部.xlsx", str(tmp_path / "外部.xlsx"), "链接.xlsx", "无效\x00路径"]:
        with pytest.raises(AppError) as error:
            storage.read(rel)
        assert error.value.status == 400
        assert error.value.code == "BAD_PATH"


def test_storage_cos(monkeypatch):
    monkeypatch.setenv("QC_STORAGE", "cos")
    with pytest.raises(NotImplementedError, match="COS 存储尚未实现"):
        get_storage()


def test_sheet_empty_values_and_pending(session, contract):
    session.add(DrumParam(ht_code=contract))
    session.commit()
    wb = openpyxl.Workbook()
    sheet_drum.build(wb.active, session, contract)
    assert wb.active["A2"].value is None
    assert wb.active["E2"].value == "待校验"
    sheet_travel.build(wb.create_sheet("大车"), session, contract)
    assert wb["大车"].max_row == 1
    wb.close()


@pytest.mark.parametrize("raw", ["12.500", Decimal("12.5000"), "12.50"])
def test_dec2_accepts_trailing_zeroes(raw):
    result = to_dec2(raw)
    assert result == Decimal("12.50")
    assert result.as_tuple().exponent == -2


def test_dec2_still_rejects_real_third_decimal():
    with pytest.raises(TooManyDecimals):
        to_dec2("12.345")
