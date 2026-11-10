"""文本字段保留大小写，匹配和数值解析继续忽略大小写。"""

from copy import deepcopy
from datetime import date

import pytest

from app.voice import parser


TODAY = date(2026, 11, 10)


def test_nameplate_case_in_parse_text_api(client):
    text = "减速机铭牌 ZQ500 样例，电机铭牌 YZR160M2-6 样例"
    response = client.post("/api/voice/parse-text", json={"text": text, "form": "travel_drive"})
    assert response.status_code == 200
    result = response.json()
    assert result["text"] == text
    assert result["fields"] == {
        "reducer_nameplate": "ZQ500 样例", "motor_nameplate": "YZR160M2-6 样例",
    }
    assert result["corrected"] == "减速机铭牌 ZQ500 样例,电机铭牌 YZR160M2-6 样例"
    assert "zq500" not in result["corrected"]
    assert [match["value"] for match in result["matched"]] == ["ZQ500 样例", "YZR160M2-6 样例"]


@pytest.mark.parametrize(("text", "field", "value"), [
    ("减速机铭牌 ＺＱ５００", "reducer_nameplate", "ZQ500"),
    ("电机铭牌 ＹＺＲ１６０Ｍ２－６", "motor_nameplate", "YZR160M2-6"),
    ("减速机铭牌 zQ500 样例", "reducer_nameplate", "zQ500 样例"),
    ("电机铭牌 yZr160M2-6 样例", "motor_nameplate", "yZr160M2-6 样例"),
    ("减速机ZQ500，噪声78", "reducer_nameplate", "ZQ500"),
    ("电机YZR", "motor_nameplate", "YZR"),
])
def test_nameplates_preserve_case_and_normalize_width(text, field, value):
    result = parser.parse_text(text, "travel_drive", TODAY)
    assert result["fields"][field] == value
    assert value in result["corrected"]


@pytest.mark.parametrize(("text", "value"), [
    ("客户名称 ABC重工（样例）", "ABC重工(样例)"),
    ("客户名称 ＡＢＣ重工（样例）", "ABC重工(样例)"),
    ("客户 AbC重工", "AbC重工"),
])
def test_customer_name_preserves_case(text, value):
    result = parser.parse_text(text, "contract", TODAY)
    assert result["fields"] == {"name": value}
    assert result["corrected"].endswith(value)


def test_corrections_preserve_case_in_surrounding_text():
    text = "减速机铭牌 ZQ500 样例，光端三百，电机铭牌 YZR160M2-6 样例"
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {"light": "300"}
    assert result["corrected"] == "减速机铭牌 ZQ500 样例,光段三百,电机铭牌 YZR160M2-6 样例"


def test_configured_matching_remains_case_insensitive(monkeypatch):
    config = deepcopy(parser.load_voice_config())
    config["corrections"]["Wrong"] = "Light"
    config["forms"]["drum"]["light"]["keywords"] = ["Light"]
    config["retract_words"].append("Undo")
    monkeypatch.setattr(parser, "load_voice_config", lambda: config)
    result = parser.parse_text("wRoNg三百，uNDo，四百，电机铭牌 YZR160M2-6", "drum", TODAY)
    assert result["fields"] == {"light": "400"}
    assert result["matched"][-1]["via"] == "retract"
    assert result["corrected"] == "Light三百,uNDo,四百,电机铭牌 YZR160M2-6"


@pytest.mark.parametrize(("text", "form", "fields"), [
    ("长度 2 CM", "drum", {"length": "20"}),
    ("直径１２５０ＭＭ", "drum", {"diameter": "1250"}),
    ("噪声 82 dB", "travel_drive", {"noise": "82"}),
    ("合同号２０２６１１１０－２０", "contract", {"ht_code": "20261110-20"}),
])
def test_numeric_and_contract_fields_keep_existing_behavior(text, form, fields):
    assert parser.parse_text(text, form, TODAY)["fields"] == fields
