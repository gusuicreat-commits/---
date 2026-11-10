"""新随机合同号从语音解析、录入到单页检验汇总导出的闭环。"""

from datetime import date
from io import BytesIO

import openpyxl
import pytest

from app.voice.parser import parse_text


@pytest.mark.parametrize("text, expected", [
    ("合同号20264827", "20264827"),
    ("合同号二零二六四八二七", "20264827"),
    ("合同号2026 4827", "20264827"),
    ("合同号２０２６０００７", "20260007"),
    ("合同号二零二六零零零七。", "20260007"),
    ("合同号20269999，客户样例重工", "20269999"),
    ("合同号20261110-20", "20261110-20"),
    ("合同号202648270", None),
    ("合同号120264827", None),
    ("合同号20264827-10", None),
    ("合同号20264827.5", None),
    ("合同号00004827", None),
    ("合同号二十", None),
    ("合同号4827", None),
])
def test_voice_codes(text, expected):
    result = parse_text(text, "contract", date(2026, 11, 10))
    assert result["fields"].get("ht_code") == expected


@pytest.mark.parametrize("spoken, code", [
    ("合同号二零二六零零零七", "20260007"),
    ("合同号二零二六一一一零杠二十", "20261110-20"),
])
def test_create_and_export_both_formats(configured, tmp_path, monkeypatch, spoken, code):
    monkeypatch.setenv("QC_EXPORT_DIR", str(tmp_path / "exports"))
    parsed = configured.post("/api/voice/parse-text", json={
        "text": spoken, "form": "contract", "source": "screen"}).json()
    assert parsed["fields"]["ht_code"] == code
    response = configured.post("/api/contracts", json={**parsed["fields"], "name": "随机号测试客户"})
    assert response.status_code == 201
    assert response.json()["ht_code"] == code
    for kind, fields in {
        "drum": {"diameter": 1250, "length": 3000, "thick": 20, "light": 310},
        "travel_drive": {"reducer_nameplate": "ZQ500", "motor_nameplate": "YZR160", "noise": 80},
        "control_cabinet": {"insulation_resistance": 500, "control_wire_torque": 1.2, "electrical_clearance": 8},
        "travel_paint": {"primer_thickness_ratio": "98", "total_thickness_ratio": 99, "adhesion_loss_ratio": 1},
    }.items():
        assert configured.post(f"/api/contracts/{code}/records/{kind}", json=fields).status_code == 201
    assert configured.get(f"/api/contracts/{code}").json()["status"] == "合格"
    exported = configured.post(f"/api/contracts/{code}/export")
    assert exported.status_code == 200
    item = configured.get("/api/exports").json()["items"][0]
    assert item["ht_code"] == code
    downloaded = configured.get(f"/api/exports/{code}/{item['date'].replace('-', '')}")
    assert downloaded.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(downloaded.content))
    try:
        assert len(wb.sheetnames) == 1
        assert wb.worksheets[0]["A5"].value == code
        assert wb.worksheets[0]["A5"].data_type == "s"
    finally:
        wb.close()
