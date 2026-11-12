"""参数名标点、跨段数值和铭牌型号规整的通用容错回归。"""

from copy import deepcopy
from datetime import date

import pytest

from app.voice import parser
from app.voice.numbers import normalize_code


TODAY = date(2026, 11, 12)


@pytest.mark.parametrize("form,text,fields", [
    ("control_cabinet", "控制线，力矩零点八", {"control_wire_torque": "0.8"}),
    ("control_cabinet", "绝缘电阻九百控制线，力矩一点零电气间隙十五",
     {"insulation_resistance": "900", "control_wire_torque": "1", "electrical_clearance": "15"}),
    ("travel_paint", "底漆厚度，达标率九十六", {"primer_thickness_ratio": "96"}),
    ("travel_paint", "面漆，厚度九十六", {"total_thickness_ratio": "96"}),
    ("drum", "厚度，二十光段三百", {"thick": "20", "light": "300"}),
    ("control_cabinet", "力矩一点二，牛米", {"control_wire_torque": "1.2"}),
    ("control_cabinet", "控制线力矩一点二，牛米", {"control_wire_torque": "1.2"}),
    ("drum", "直径，一千二百五十", {"diameter": "1250"}),
    ("travel_paint", "底漆，九十四，九十五九十八", {"primer_thickness_ratio": "94,95,98"}),
    ("travel_drive", "减速机名牌zq五百噪声八十", {"reducer_nameplate": "ZQ500", "noise": "80"}),
    ("control_cabinet", "绝缘电阻六百兆o电器间隙十毫米",
     {"insulation_resistance": "600", "electrical_clearance": "10"}),
    ("control_cabinet", "绝缘电阻六百兆O", {"insulation_resistance": "600"}),
    ("drum", "厚度，是大概二十光段三百", {"thick": "20", "light": "300"}),
    ("drum", "直,径一千二百五十", {"diameter": "1250"}),
    ("control_cabinet", "控 、 制线 ， 力矩一点二 , N·m", {"control_wire_torque": "1.2"}),
    ("drum", "长，三百", {"length": "300"}),
])
def test_punctuation_and_cross_clause_values(form, text, fields):
    assert parser.parse_text(text, form, TODAY)["fields"] == fields


@pytest.mark.parametrize("said,expected", [
    ("zq五百", "ZQ500"),
    ("YZR一百六十M二杠六", "YZR160M2-6"),
    ("ZQ五零零 样例", "ZQ500样例"),
    ("ZQ一六零", "ZQ160"),
    ("ＺＱ五百 横杠 幺洞", "ZQ500-10"),
    ("AB二横三减四杠五", "AB2-3-4-5"),
    ("符合要求", "符合要求"),
    ("样例", "样例"),
    ("一百样例", "一百样例"),
    ("一百ZQ", "100ZQ"),
    ("样例二3", "样例23"),
    ("横向样例", "横向样例"),
])
def test_normalize_code(said, expected):
    assert normalize_code(said) == expected


@pytest.mark.parametrize("keyword,field", [
    ("减速机铭牌", "reducer_nameplate"), ("电机铭牌", "motor_nameplate"),
])
@pytest.mark.parametrize("said,expected", [
    ("zq五百", "ZQ500"), ("YZR一百六十M二杠六", "YZR160M2-6"),
    ("ZQ五零零 样例", "ZQ500样例"), ("符合要求", "符合要求"),
])
def test_code_fields(keyword, field, said, expected):
    assert parser.parse_text(keyword + said, "travel_drive", TODAY)["fields"] == {field: expected}


def test_other_text_fields_are_unchanged():
    assert parser.parse_text("客户zq五百 样例", "contract", TODAY)["fields"] == {"name": "zq五百 样例"}


@pytest.mark.parametrize("text", ["控制线，你等一下", "厚度，你等一下", "直径，", "直径，是厚度二十"])
def test_pending_field_requires_numeric_start(text):
    form = "control_cabinet" if text.startswith("控制线") else "drum"
    result = parser.parse_text(text, form, TODAY)
    assert result["rejected"][0]["reason"] == "没有说出数值"


def test_longer_homophone_wins_over_exact_alias():
    result = parser.parse_text("减速机名牌zq五百", "travel_drive", TODAY)
    assert result["matched"] == [{"field": "reducer_nameplate", "keyword": "减速机铭牌", "via": "pinyin", "value": "ZQ500"}]


def test_exact_only_field_does_not_extend_by_sound():
    assert parser.parse_text("底漆后读九十六", "travel_paint", TODAY)["fields"] == {}


def test_keyword_punctuation_only_uses_exact_current_form_words():
    result = parser.parse_text("控制线，力矩零点八", "drum", TODAY)
    assert result["corrected"] == "控制线,力矩零点八"
    result = parser.parse_text("绝缘，电祖六百", "control_cabinet", TODAY)
    assert result["corrected"] == "绝缘,电祖六百"


def test_keyword_punctuation_is_case_insensitive(monkeypatch):
    config = deepcopy(parser.load_voice_config())
    config["forms"]["drum"]["light"]["keywords"] = ["Light"]
    monkeypatch.setattr(parser, "load_voice_config", lambda: config)
    result = parser.parse_text("lI，gHt三百", "drum", TODAY)
    assert result["fields"] == {"light": "300"}
    assert result["corrected"] == "lIgHt三百"


@pytest.mark.parametrize("form,text,corrected", [
    ("control_cabinet", "控制线，力矩零点八", "控制线力矩零点八"),
    ("drum", "直经，１２５０，毫米", "直径,1250毫米"),
    ("drum", "直径二，分贝", "直径二,分贝"),
])
def test_corrected_reflects_separator_removal(form, text, corrected):
    assert parser.parse_text(text, form, TODAY)["corrected"] == corrected


def test_negative_original_clauses_stay_aligned_after_removal():
    result = parser.parse_text("控制线，力矩一点二，牛米，木有测电器间隙", "control_cabinet", TODAY)
    assert result["fields"] == {"control_wire_torque": "1.2"}
    assert result["rejected"] == [{"field": "electrical_clearance", "said": "木有测电器间隙", "reason": "说还没测"}]


def test_negative_clause_preserves_original_width_and_typo():
    result = parser.parse_text("直径１２５０，毫米，木有测后度", "drum", TODAY)
    assert result["rejected"] == [{"field": "thick", "said": "木有测后度", "reason": "说还没测"}]
