"""语音文字解析验收，不加载识别引擎和数据库。"""

import json
from datetime import date

import pytest

from app.voice.parser import _check_ht_code, hotwords, load_voice_config, parse_text


TODAY = date(2026, 11, 10)


@pytest.mark.parametrize(("form", "text", "fields"), [
    ("drum", "直径一千二百五十", {"diameter": "1250"}),
    ("drum", "直径1.25米", {"diameter": "1250"}),
    ("drum", "厚度十二点五毫米，光段三百", {"thick": "12.5", "light": "300"}),
    ("travel_drive", "噪音八十二分贝", {"noise": "82"}),
    ("drum", "直经一千二百五十", {"diameter": "1250"}),
    ("drum", "光端三百", {"light": "300"}),
    ("drum", "直径1250，不对，直径1252", {"diameter": "1252"}),
    ("drum", "今天天气不错", {}), ("drum", "噪声80", {}),
    ("drum", "厚度十二", {"thick": "12"}),
    ("contract", "合同号 20261110-10", {"ht_code": "20261110-10"}),
    ("contract", "合同号 20261110 10", {"ht_code": "20261110-10"}),
    ("contract", "合同号二零二六一一一零杠一零", {"ht_code": "20261110-10"}),
    ("contract", "合同号二十", {"ht_code": "20261110-20"}),
    ("contract", "合同号一二三", {}),
    ("drum", "只径一千", {"diameter": "1000"}),
    ("drum", "光短三百", {"light": "300"}), ("drum", "光线三百", {}),
    ("drum", "直径1250长度3000厚度20光段310",
     {"diameter": "1250", "length": "3000", "thick": "20", "light": "310"}),
    ("drum", "直径1250，不对，1252", {"diameter": "1252"}),
    ("drum", "长度3米", {"length": "3000"}),
    ("drum", "直径125公分", {"diameter": "1250"}),
    ("travel_drive", "噪声80毫米", {}),
    ("drum", "直径1.2345米", {"diameter": "1234.5"}),
    ("travel_drive", "电机铭牌 YZR160M2-6", {"motor_nameplate": "YZR160M2-6"}),
    ("travel_drive", "电机声音有点大", {}),
    ("travel_drive", "减速机ZQ500，噪声78", {"reducer_nameplate": "ZQ500", "noise": "78"}),
    ("contract", "日期今天", {"date": "2026-11-10"}),
    ("contract", "日期昨天", {"date": "2026-11-09"}),
    ("contract", "日期十一月八号", {"date": "2026-11-08"}),
    ("contract", "日期2月30日", {}),
    ("contract", "客户河南某某机械有限公司，合同号20261110-20",
     {"name": "河南某某机械有限公司", "ht_code": "20261110-20"}),
])
def test_acceptance_cases(form, text, fields):
    result = parse_text(text, form, TODAY)
    assert result["fields"] == fields
    assert result["text"] == text
    assert result["message"] == (None if fields else "未识别到参数")


@pytest.mark.parametrize("form", ["contract", "drum", "travel_drive"])
@pytest.mark.parametrize("text", [
    "我先去拿一下尺子", "这台明天再测", "后面再说", "长时间没校准了", "厂长来了",
    "先等一下", "这个光线不太好", "电话等会儿回", "厚道一点",
])
def test_ordinary_sentences_are_ignored(form, text):
    assert parse_text(text, form, TODAY)["fields"] == {}


@pytest.mark.parametrize(("form", "text", "notes"), [
    ("drum", "直径1.25米", {"diameter": "已从米换算"}),
    ("drum", "直径125公分", {"diameter": "已从厘米换算"}),
    ("drum", "长度 2 CM", {"length": "已从厘米换算"}),
    ("contract", "合同号二十", {"ht_code": "已补当天日期"}),
    ("drum", "直径1米，直径1250", {}),
    ("contract", "合同号二十，合同号20261109-30", {}),
])
def test_notes(form, text, notes):
    assert parse_text(text, form, TODAY)["notes"] == notes


@pytest.mark.parametrize(("text", "field", "keyword", "via", "value"), [
    ("只径一千", "diameter", "直径", "pinyin", "1000"),
    ("光短三百", "light", "光段", "pinyin", "300"),
    ("直径1250，不对，1252", "diameter", "直径", "retract", "1252"),
    ("长12", "length", "长", "exact", "12"),
    ("壁厚十二", "thick", "壁厚", "exact", "12"),
])
def test_match_provenance(text, field, keyword, via, value):
    assert parse_text(text, "drum", TODAY)["matched"][-1] == {
        "field": field, "keyword": keyword, "via": via, "value": value,
    }


@pytest.mark.parametrize(("form", "text", "field", "said", "reason"), [
    ("travel_drive", "噪声80毫米", "noise", "80毫米", "单位不对"),
    ("drum", "直径80db", "diameter", "80db", "单位不对"),
    ("drum", "直径", "diameter", "", "没有说出数值"),
    ("contract", "合同号", "ht_code", "", "没有说出数值"),
    ("contract", "客户", "name", "", "没有内容"),
    ("contract", "合同号123", "ht_code", "123", "合同号格式不对"),
    ("travel_drive", "电机声音有点大", "motor_nameplate", "声音有点大", "没有内容"),
])
def test_rejected_values(form, text, field, said, reason):
    result = parse_text(text, form, TODAY)
    assert result["fields"] == {}
    assert result["rejected"] == [{"field": field, "said": said, "reason": reason}]


@pytest.mark.parametrize(("text", "expected"), [
    ("直径是：１２５０ＭＭ；长度等于 ３０００！厚度为二十\n光段:310",
     {"diameter": "1250", "length": "3000", "thick": "20", "light": "310"}),
    ("直径噪声80", {}),
    ("直径1250噪声80长度三百", {"diameter": "1250", "length": "300"}),
    ("直径1.235毫米", {"diameter": "1.24"}),
    ("直径0.004毫米", {"diameter": "0"}),
    ("直径0.005毫米", {"diameter": "0.01"}),
    ("直径1厘米，更正为2米", {"diameter": "2000"}),
    ("直径1米，厚度5，不对，6", {"diameter": "1000", "thick": "6"}),
    ("不对，1252", {}),
    ("直径一千，直径分贝", {"diameter": "1000"}),
    ("厚道一点，长时间十年", {}),
    ("长 12，厚为二十", {}),
])
def test_normalization_boundaries_rounding_and_retract(text, expected):
    assert parse_text(text, "drum", TODAY)["fields"] == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("合同2026111010", "20261110-10"),
    ("合同号二零二六一一一零杠二十", "20261110-20"),
    ("合同号20261110横杠九十", "20261110-90"),
    ("合同号20261110杠十", "20261110-10"),
    ("合同号 20", "20261110-20"),
    ("合同号20260230-10", None), ("合同号20261110-25", None),
    ("合同号20261110-00", None), ("合同号一百", None),
    ("合同号序号二十", None), ("合同号20261110-100", None),
])
def test_contract_code_formats(text, expected):
    assert parse_text(text, "contract", TODAY)["fields"].get("ht_code") == expected


@pytest.mark.parametrize(("said", "expected"), [
    ("前天", "2026-11-08"), ("2024年2月29日", "2024-02-29"),
    ("2026年11月9日", "2026-11-09"), ("二零二六年十一月九日", "2026-11-09"),
    ("2026-11-09", "2026-11-09"), ("2025年2月29日", None),
    ("2026-13-01", None), ("13月1日", None), ("", None),
])
def test_date_formats(said, expected):
    assert parse_text("日期" + said, "contract", TODAY)["fields"].get("date") == expected


def test_relative_date_across_year_boundary():
    assert parse_text("日期前天", "contract", date(2026, 1, 1))["fields"]["date"] == "2025-12-30"


@pytest.mark.parametrize(("unit", "expected", "note"), [
    ("", "2.5", None), ("毫米", "2.5", None), ("mm", "2.5", None),
    ("厘米", "25", "已从厘米换算"), ("公分", "25", "已从厘米换算"),
    ("cm", "25", "已从厘米换算"), ("米", "2500", "已从米换算"),
    ("m", "2500", "已从米换算"),
])
def test_length_unit_matrix(unit, expected, note):
    result = parse_text("直径二点五 " + unit, "drum", TODAY)
    assert result["fields"] == {"diameter": expected}
    assert result["notes"].get("diameter") == note


@pytest.mark.parametrize("unit", ["", "分贝", "db", "毫米", "厘米", "公分", "mm", "cm", "米", "m"])
def test_noise_unit_matrix(unit):
    result = parse_text("噪声82 " + unit, "travel_drive", TODAY)
    if unit in {"", "分贝", "db"}:
        assert result["fields"] == {"noise": "82"}
    else:
        assert result["fields"] == {}
        assert result["rejected"][0]["reason"] == "单位不对"


@pytest.mark.parametrize("form", ["contract", "drum", "travel_drive"])
def test_empty_text(form):
    assert parse_text("", form, TODAY) == {
        "text": "", "corrected": "", "fields": {}, "notes": {},
        "matched": [], "rejected": [], "message": "未识别到参数",
    }


@pytest.mark.parametrize(("form", "keyword", "field", "limit"), [
    ("contract", "客户名称", "name", 128),
    ("travel_drive", "减速机铭牌", "reducer_nameplate", 255),
    ("travel_drive", "电机铭牌", "motor_nameplate", 255),
])
def test_text_limits_and_strict_keywords(form, keyword, field, limit):
    result = parse_text(keyword + "甲" * 300, form, TODAY)
    assert result["fields"] == {field: "甲" * limit}
    assert result["matched"][0]["keyword"] == keyword


@pytest.mark.parametrize("form", ["", "unknown", "Drum"])
def test_invalid_form(form):
    with pytest.raises(ValueError, match="不支持的表单类型"):
        parse_text("直径1250", form, TODAY)


@pytest.mark.parametrize(("value", "valid"), [
    ("20261110-10", True), ("20240229-90", True), ("20230229-10", False),
    ("00001110-10", False), ("20261110-11", False), ("2026111010", False),
])
def test_check_contract_code(value, valid):
    assert _check_ht_code(value) is valid


def test_corrections_and_hotwords():
    result = parse_text("直经１．２５米，光端三百", "drum", TODAY)
    assert result["corrected"] == "直径1.25米,光段三百"
    assert result["fields"] == {"diameter": "1250", "light": "300"}
    words = hotwords()
    assert words == load_voice_config()["hotwords"]
    words.clear()
    assert "卷筒" in hotwords()


def test_config_path_from_environment_and_independent_of_cwd(tmp_path, monkeypatch):
    config = load_voice_config()
    path = tmp_path / "voice.json"
    path.write_text(json.dumps({**config, "hotwords": ["测试热词"]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    load_voice_config.cache_clear()
    try:
        assert "卷筒" in hotwords()
        monkeypatch.setenv("VOICE_CONFIG_PATH", str(path))
        load_voice_config.cache_clear()
        assert hotwords() == ["测试热词"]
    finally:
        load_voice_config.cache_clear()


@pytest.mark.parametrize(("form", "text"), [
    ("drum", "长度等一下再测"),
    ("travel_drive", "噪声有一点大"),
    ("drum", "厚度一会儿再量"),
    ("drum", "直径大概要等三分钟"),
    ("travel_drive", "噪声在测了三次之后再说"),
    ("drum", "长度是大约一会儿再量"),
])
def test_numbers_must_be_complete_measurements_at_value_start(form, text):
    result = parse_text(text, form, TODAY)
    assert result["fields"] == {}
    assert result["notes"] == {}
    assert result["matched"] == []
    assert result["rejected"][0]["reason"] == "没有说出数值"


@pytest.mark.parametrize(("form", "text", "fields"), [
    ("drum", "直径大概一千二百五", {"diameter": "1250"}),
    ("drum", "直径约1250", {"diameter": "1250"}),
    ("travel_drive", "运行噪声八十三", {"noise": "83"}),
])
def test_numeric_value_prefixes(form, text, fields):
    assert parse_text(text, form, TODAY)["fields"] == fields


@pytest.mark.parametrize("fillers", [
    " ", "是", "为", "等于", ":", "：", "大概", "大约", "约", "差不多", "在",
    " 在约： 是大概为 等于差不多大约 ", "大约在是约在",
])
@pytest.mark.parametrize(("form", "keyword", "field"), [
    ("drum", "长度", "length"), ("travel_drive", "噪声", "noise"),
])
def test_numeric_fillers_allow_repeated_arbitrary_combinations(form, keyword, field, fillers):
    result = parse_text(keyword + fillers + "八十三", form, TODAY)
    assert result["fields"] == {field: "83"}
    assert result["rejected"] == []


def test_numeric_fillers_do_not_strip_text_fields():
    result = parse_text("客户大约机械有限公司", "contract", TODAY)
    assert result["fields"] == {"name": "大约机械有限公司"}


@pytest.mark.parametrize(("text", "fields"), [
    ("长度两米九九八", {"length": "2998"}),
    ("长度一米二", {"length": "1200"}),
    ("直径1米25", {"diameter": "1250"}),
    ("长度三米", {"length": "3000"}),
    ("长度3米，厚度二十", {"length": "3000", "thick": "20"}),
    ("光段一m〇幺两", {"light": "1012"}),
    ("厚度0米零二五", {"thick": "25"}),
    ("长度2M9九8", {"length": "2998"}),
    ("长度一米零零零五", {"length": "1000.5"}),
    ("长度一米，二十", {"length": "1000"}),
])
def test_spoken_decimal_digits_after_meters(text, fields):
    result = parse_text(text, "drum", TODAY)
    assert result["fields"] == fields
    converted = next(iter(fields))
    assert result["notes"] == {converted: "已从米换算"}
    assert result["rejected"] == []


@pytest.mark.parametrize("text", [
    "长度1.2米5", "直径一点二m五", "厚度2.75米25",
    "长度一米二十", "长度一米二点五",
])
def test_ambiguous_meter_expressions_are_rejected(text):
    result = parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
    assert result["notes"] == {}
    assert result["matched"] == []
    assert result["rejected"][0]["reason"] == "数值说法无法确定"


@pytest.mark.parametrize("text", ["直劲一千二", "只劲一千二"])
def test_pinyin_matches_polyphonic_characters(text):
    result = parse_text(text, "drum", TODAY)
    assert result["fields"] == {"diameter": "1200"}
    assert result["matched"] == [{
        "field": "diameter", "keyword": "直径", "via": "pinyin", "value": "1200",
    }]


@pytest.mark.parametrize("text", ["直近一千二", "资径一千二"])
def test_pinyin_does_not_match_different_pronunciations(text):
    result = parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
