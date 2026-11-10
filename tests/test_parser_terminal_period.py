"""真实 ASR 句尾英文句号回归，保留小数、单位与拒收边界。"""
from datetime import date

import pytest

from app.voice.parser import parse_text


@pytest.mark.parametrize("form,text,expected", [
    ("travel_drive", "噪声八十五db.", {"noise": "85"}),
    ("travel_drive", "噪声八十五DB.", {"noise": "85"}),
    ("travel_drive", "噪音八十五dB.", {"noise": "85"}),
    ("travel_drive", "噪声八十五分贝.", {"noise": "85"}),
    ("travel_drive", "噪声是一百DB.", {"noise": "100"}),
    ("travel_drive", "噪声85.5db.", {"noise": "85.5"}),
    ("travel_drive", "噪声85.", {"noise": "85"}),
    ("travel_drive", "噪声８５ＤＢ．", {"noise": "85"}),
    ("travel_drive", "噪声八十五分贝嘞.", {"noise": "85"}),
    ("drum", "直径1250mm.", {"diameter": "1250"}),
    ("drum", "厚度十二点五毫米.", {"thick": "12.5"}),
    ("drum", "直径一点二五米.", {"diameter": "1250"}),
    ("drum", "长度两米九九八.", {"length": "2998"}),
    ("control_cabinet", "绝缘电阻180兆欧.", {"insulation_resistance": "180"}),
    ("control_cabinet", "控制线力矩1.05N.m.", {"control_wire_torque": "1.05"}),
    ("travel_paint", "脱落率0.5%.", {"adhesion_loss_ratio": "0.5"}),
])
def test_terminal_period_after_numeric_value(form, text, expected):
    result = parse_text(text, form, date(2026, 11, 10))
    assert result["fields"] == expected
    assert result["rejected"] == []
    assert result["text"] == text  # 原始证据不丢失句号。


def test_recorded_multi_field_utterance():
    result = parse_text("减速机铭牌YZR电机名牌QCX噪声八十五db.", "travel_drive", date(2026, 11, 10))
    assert result["fields"]["noise"] == "85"
    assert result["rejected"] == []


@pytest.mark.parametrize("form,text", [
    ("travel_drive", "噪声八十多db."),
    ("travel_drive", "噪声85db.5"),
    ("travel_drive", "噪声85..5db."),
    ("travel_drive", "噪声85db.."),
    ("travel_drive", "噪声85db.还有其他内容"),
    ("travel_drive", "噪声.85db."),
    ("travel_drive", "噪声85毫米."),
    ("drum", "直径一点几米."),
])
def test_period_does_not_hide_invalid_or_uncertain_readings(form, text):
    result = parse_text(text, form, date(2026, 11, 10))
    assert result["fields"] == {}
    assert result["rejected"]


def test_nameplate_punctuation_is_preserved():
    result = parse_text("电机铭牌YZR160.", "travel_drive", date(2026, 11, 10))
    assert result["fields"] == {"motor_nameplate": "YZR160."}
