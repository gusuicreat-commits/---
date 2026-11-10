"""河南话语气词、估量词、口音关键词与否定说法的通用解析验收。"""

from copy import deepcopy
from datetime import date

import pytest

from app.voice import parser


TODAY = date(2026, 11, 10)


@pytest.mark.parametrize("particle", list("嘞哩咧唻啦呢啊呀哈嘛了吧哟") + ["嘞啊", "哩咧呢"])
@pytest.mark.parametrize(("reading", "value"), [
    ("三百", "300"), ("十二点五毫米", "12.5"), ("1.25米", "1250"),
    ("一米二", "1200"), ("125CM", "1250"),
])
def test_particles_after_complete_readings(particle, reading, value):
    result = parser.parse_text("直径" + reading + particle, "drum", TODAY)
    assert result["fields"] == {"diameter": value}
    assert result["rejected"] == []


@pytest.mark.parametrize("text", [
    "直径嘞三百", "直径有点大嘞", "直径三百了以后再说", "直径三百毫米嘞还有一段",
    "中了", "得劲", "有点大嘞", "三百了",
])
def test_particles_do_not_turn_ordinary_words_into_readings(text):
    assert parser.parse_text(text, "drum", TODAY)["fields"] == {}


@pytest.mark.parametrize("filler", [
    "有", "得有", "能有", "估摸", "估摸着", "约莫", "差不离", "大约摸", "大概齐", "得", "整",
    "估摸着得有", "差不离能有", "大概齐整",
])
@pytest.mark.parametrize(("form", "keyword", "field", "reading", "value"), [
    ("drum", "直径", "diameter", "一千二", "1200"),
    ("drum", "光段", "light", "三百一十毫米嘞", "310"),
    ("travel_drive", "噪声", "noise", "八十二分贝哩", "82"),
])
def test_estimates_and_existence_words(form, keyword, field, reading, value, filler):
    assert parser.parse_text(keyword + filler + reading, form, TODAY)["fields"] == {field: value}


@pytest.mark.parametrize("text", ["直径有一点大", "直径得劲", "直径估摸着等会量", "直径三百整"])
def test_fillers_only_precede_complete_values(text):
    assert parser.parse_text(text, "drum", TODAY)["fields"] == {}


@pytest.mark.parametrize(("form", "keyword", "field", "value"), [
    ("contract", "客户名称", "name", "有得机械有限公司"),
    ("travel_drive", "电机铭牌", "motor_nameplate", "整有YZR160M2-6嘞"),
    ("travel_drive", "减速机铭牌", "reducer_nameplate", "估摸着ZQ500了"),
])
def test_fillers_and_particles_preserve_text_fields(form, keyword, field, value):
    assert parser.parse_text(keyword + value, form, TODAY)["fields"] == {field: value}


@pytest.mark.parametrize(("spoken", "standard", "field", "reading", "value", "form"), [
    ("子径", "直径", "diameter", "一千二百五十", "1250", "drum"),
    ("资径", "直径", "diameter", "一千二", "1200", "drum"),
    ("直近", "直径", "diameter", "一千二", "1200", "drum"),
    ("藏度", "长度", "length", "三千", "3000", "drum"),
    ("噪森", "噪声", "noise", "八十", "80", "travel_drive"),
    ("噪应", "噪音", "noise", "八十", "80", "travel_drive"),
])
def test_dialect_keywords_are_marked_and_corrected(spoken, standard, field, reading, value, form):
    result = parser.parse_text(spoken + reading, form, TODAY)
    assert result["fields"] == {field: value}
    assert result["corrected"] == standard + reading
    assert result["matched"] == [{
        "field": field, "keyword": standard, "via": "fuzzy", "value": value, "fuzzy": True,
    }]


@pytest.mark.parametrize(("spoken", "standard"), [
    ("三度", "山度"), ("山度", "三度"), ("南度", "蓝度"), ("蓝度", "南度"),
    ("申度", "生度"), ("生度", "申度"), ("津度", "京度"), ("京度", "津度"),
    ("长度", "藏度"), ("藏度", "长度"), ("子径", "直径"), ("直径", "子径"),
])
def test_dialect_pairs_work_in_both_directions(monkeypatch, spoken, standard):
    config = deepcopy(parser.load_voice_config())
    config["forms"]["drum"] = {"diameter": config["forms"]["drum"]["diameter"]}
    config["forms"]["drum"]["diameter"]["keywords"] = [standard]
    monkeypatch.setattr(parser, "load_voice_config", lambda: config)
    result = parser.parse_text(spoken + "1250", "drum", TODAY)
    assert result["fields"] == {"diameter": "1250"}
    assert result["matched"][0]["fuzzy"] is True


@pytest.mark.parametrize("text", [
    "子径有点大", "子径等会量1250", "子径", "子径得劲", "子径嘞三百", "常三百", "伙度三百",
])
def test_fuzzy_matches_require_numeric_content_and_multiple_characters(text):
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
    assert result["corrected"] == text


def test_exact_and_homophone_hits_take_priority():
    exact = parser.parse_text("直径一千", "drum", TODAY)
    homophone = parser.parse_text("只径一千", "drum", TODAY)
    assert exact["matched"][0]["via"] == "exact"
    assert homophone["matched"][0]["via"] == "pinyin"
    assert "fuzzy" not in exact["matched"][0]
    assert "fuzzy" not in homophone["matched"][0]


@pytest.mark.parametrize(("keyword", "expected_via"), [("子径", "exact"), ("资径", "pinyin")])
def test_fuzzy_candidate_cannot_override_higher_priority_hit(monkeypatch, keyword, expected_via):
    config = deepcopy(parser.load_voice_config())
    config["forms"]["drum"]["light"]["keywords"] = [keyword]
    monkeypatch.setattr(parser, "load_voice_config", lambda: config)
    result = parser.parse_text("子径300", "drum", TODAY)
    assert result["fields"] == {"light": "300"}
    assert result["matched"][0]["via"] == expected_via


def test_fuzzy_matching_uses_whole_finals(monkeypatch):
    config = deepcopy(parser.load_voice_config())
    config["forms"]["drum"]["light"]["keywords"] = ["江度"]
    monkeypatch.setattr(parser, "load_voice_config", lambda: config)
    assert parser.parse_text("间度300", "drum", TODAY)["fields"] == {}


def test_fuzzy_correction_preserves_other_content_and_exact_hits():
    result = parser.parse_text("直径1250，子径1252；噪森82。光段300", "drum", TODAY)
    assert result["fields"] == {"diameter": "1252", "light": "300"}
    assert result["corrected"] == "直径1250,直径1252;噪森82。光段300"


@pytest.mark.parametrize("negative", ["没有", "木有", "冇", "没"])
@pytest.mark.parametrize("action", ["", "测", "量"])
@pytest.mark.parametrize(("keyword", "field"), [("直径", "diameter"), ("厚度", "thick"), ("光段", "light")])
def test_negation_before_keywords_keeps_original_clause(negative, action, keyword, field):
    text = negative + action + keyword
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
    assert result["rejected"] == [{"field": field, "said": text, "reason": "说还没测"}]


@pytest.mark.parametrize("negative", ["没测", "没量", "还没测"])
@pytest.mark.parametrize(("keyword", "field"), [("直径", "diameter"), ("厚度", "thick"), ("光段", "light")])
def test_negation_after_keywords(negative, keyword, field):
    text = keyword + negative
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
    assert result["rejected"] == [{"field": field, "said": text, "reason": "说还没测"}]


@pytest.mark.parametrize("separator", ["，", "﹐", "︐"])
def test_negative_said_keeps_source_spelling_and_punctuation_boundaries(separator):
    result = parser.parse_text("，直径1250，" + separator + "木有测后度；光段300", "drum", TODAY)
    assert result["fields"] == {"diameter": "1250", "light": "300"}
    assert result["rejected"] == [{"field": "thick", "said": "木有测后度", "reason": "说还没测"}]


@pytest.mark.parametrize(("text", "field"), [
    ("没测长", "length"), ("木有量厚", "thick"), ("厚还没测", "thick"),
])
def test_single_character_exact_keywords_support_measurement_negation(text, field):
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {}
    assert result["rejected"] == [{"field": field, "said": text, "reason": "说还没测"}]


@pytest.mark.parametrize("text", ["直径有1250", "直径1250了", "没有尺子直径1250", "直径1250，没带尺子"])
def test_non_measurement_negation_does_not_reject_values(text):
    result = parser.parse_text(text, "drum", TODAY)
    assert result["fields"] == {"diameter": "1250"}
    assert result["rejected"] == []


@pytest.mark.parametrize(("text", "field", "standard", "value"), [
    ("直井1250", "diameter", "直径1250", "1250"),
    ("厚肚20", "thick", "厚度20", "20"),
    ("壁后20", "thick", "壁厚20", "20"),
])
def test_configured_dialect_corrections(text, field, standard, value):
    result = parser.parse_text(text, "drum", TODAY)
    assert result["corrected"] == standard
    assert result["fields"] == {field: value}


def test_learning_example_is_not_added_to_corrections():
    assert "光蛋" not in parser.load_voice_config()["corrections"]
    assert parser.parse_text("光蛋三百", "drum", TODAY)["fields"] == {}
