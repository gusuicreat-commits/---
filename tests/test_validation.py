from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.dec import fmt, to_dec2
from app.errors import BadNumber, TooManyDecimals
from app.fields import load_fields, validated_fields
from app.models import DrumParam, Rule
from app.services.validation import judge, validate_record

PARAMETERS = [(table, field["name"], field["label"])
              for table in load_fields() for field in validated_fields(table)]


def rule_set(table):
    return {field["name"]: Rule(table_name=table, name=field["name"],
                                min=Decimal("10.00"), max=Decimal("20.00"))
            for field in validated_fields(table)}


@pytest.mark.parametrize("table,name,label", PARAMETERS)
@pytest.mark.parametrize("value,qualified", [
    ("10.00", True), ("20.00", True), ("9.99", False), ("20.01", False),
])
def test_boundaries(table, name, label, value, qualified):
    rules = rule_set(table)
    values = {field: Decimal("15") for field in rules}
    values[name] = Decimal(value)
    result = judge(table, values, rules)
    item = next(item for item in result["items"] if item["field"] == name)
    assert item["ok"] is qualified
    assert result["is_qualified"] == int(qualified)
    if not qualified:
        if name == 'primer_thickness_ratio':
            assert item['reason'] == f'第1个测点 {fmt(Decimal(value))} 不合格，超出 10～20'
        else:
            assert item["reason"] == f"{label} {fmt(Decimal(value))} {item['unit']}，超出 10～20"


@pytest.mark.parametrize("table,name,label", PARAMETERS)
def test_empty_value_first(table, name, label):
    rules = rule_set(table)
    values = {field: Decimal("15") for field in rules}
    values[name] = None
    rules.pop(name)
    result = judge(table, values, rules)
    assert result["is_qualified"] == 0
    assert result["failures"] == [f"{label}未填写"]


@pytest.mark.parametrize("table,name,label", PARAMETERS)
@pytest.mark.parametrize("missing", ["rule", "min", "max", "both"])
def test_unconfigured_rules(table, name, label, missing):
    rules = rule_set(table)
    values = {field: Decimal("15") for field in rules}
    if missing == "rule":
        rules.pop(name)
    else:
        if missing in {"min", "both"}:
            rules[name].min = None
        if missing in {"max", "both"}:
            rules[name].max = None
    result = judge(table, values, rules)
    assert result["is_qualified"] == 0
    assert result["failures"] == [f"规则未配置：{label}"]


def test_one_failure_and_nameplates():
    rules = rule_set("drum_param")
    result = judge("drum_param", {"diameter": Decimal("21"), "length": Decimal("15"),
                   "thick": Decimal("15"), "light": Decimal("15")}, rules)
    assert result["is_qualified"] == 0
    assert len(result["failures"]) == 1
    for text_value in [None, "任意铭牌", "!!!", "x" * 255]:
        result = judge("travel_drive_param", {"noise": Decimal("15"),
                       "reducer_nameplate": text_value, "motor_nameplate": text_value},
                       rule_set("travel_drive_param"))
        assert result["is_qualified"] == 1
        assert len(result["items"]) == 1


def test_sqlite_boundary(session, configured):
    record = DrumParam(ht_code="20261110-10", diameter=to_dec2("1260.00"),
                       length=to_dec2(3000), thick=to_dec2(20), light=to_dec2(310))
    session.add(record)
    session.commit()
    record_id = record.id
    session.expunge_all()
    record = session.get(DrumParam, record_id)
    assert record.diameter == Decimal("1260.00")
    rule = session.scalar(select(Rule).where(Rule.name == "diameter"))
    assert rule.max == Decimal("1260")
    assert validate_record(session, "drum_param", record)["is_qualified"] == 1
    assert session.execute(text("SELECT diameter, typeof(diameter) FROM drum_param")).one() == (126000, "integer")


@pytest.mark.parametrize("raw", ["0.01", "0.1", "1234.56", "99999.99"])
def test_dec2_roundtrip(session, raw):
    record = DrumParam(ht_code="20261110-10", diameter=to_dec2(raw))
    session.add(record)
    session.commit()
    record_id = record.id
    session.expunge_all()
    assert session.get(DrumParam, record_id).diameter == Decimal(raw)


@pytest.mark.parametrize("raw", ["12.345", Decimal("0.001"), 12.345])
def test_too_many_decimals(raw):
    with pytest.raises(TooManyDecimals):
        to_dec2(raw)


@pytest.mark.parametrize("raw", ["abc", "nan", "Infinity", float("nan"), float("inf"), [], {}, True])
def test_bad_number(raw):
    with pytest.raises(BadNumber):
        to_dec2(raw)


@pytest.mark.parametrize("raw,expected", [
    ("１２.５", Decimal("12.50")), (12.5, Decimal("12.50")),
    (12, Decimal("12.00")), (Decimal("0.1"), Decimal("0.10")),
    (None, None), ("", None), ("　 ", None), (" 12.50 ", Decimal("12.50")),
])
def test_to_dec2(raw, expected):
    assert to_dec2(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("1262.00", "1262"), ("12.50", "12.5"), ("0.10", "0.1"),
    ("0.00", "0"), ("1E+3", "1000"), ("-12.50", "-12.5"),
])
def test_fmt(raw, expected):
    assert fmt(Decimal(raw) if raw is not None else None) == expected
