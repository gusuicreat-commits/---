"""数字解析用例，包含口语省略、逐位读数及不合法结构。"""

from decimal import Decimal

import pytest

from app.voice.numbers import cn_digits_to_ascii, find_number, parse_cn_number


@pytest.mark.parametrize(("said", "expected"), [
    ("1250", "1250"), ("12.5", "12.5"),
    ("一千二百五十", "1250"), ("三十五", "35"), ("十二", "12"),
    ("十", "10"), ("两千", "2000"), ("一万零五百", "10500"),
    ("二百零五", "205"), ("一千二", "1200"), ("三百五", "350"),
    ("一万二", "12000"), ("十二点五", "12.5"), ("一点二五", "1.25"),
    ("零点五", "0.5"), ("一二五零", "1250"), ("一二五", "125"),
    ("〇", "0"), ("幺二两", "122"), ("零", "0"),
    ("一千零二", "1002"), ("三百零五", "305"), ("一万零二", "10002"),
    ("一万二千三百四十五", "12345"), ("九千九百九十九", "9999"),
    ("十万", "100000"), ("一万二千三", "12300"), ("一千二点五", "1200.5"),
])
def test_parse_number(said, expected):
    assert parse_cn_number(said) == Decimal(expected)


@pytest.mark.parametrize("said", [
    "千千", "", "点五", "一点", "一点点五", "一百百", "一十百", "一万万",
    "一百二十三四", "一二百", "毫", "1.2.3", "-12", "一百点十", "一百零十",
])
def test_invalid_number_returns_none(said):
    assert parse_cn_number(said) is None


@pytest.mark.parametrize(("said", "expected"), [
    ("直径一千，长度1250", (Decimal("1000"), 2, 4)),
    ("直径1250，长度三百", (Decimal("1250"), 2, 6)),
    ("为十二点五毫米", (Decimal("12.5"), 1, 5)),
    ("读数 12.5 mm", (Decimal("12.5"), 3, 7)),
    ("千千以及三百", (Decimal("300"), 4, 6)),
    ("无数字", None), ("点五", None),
])
def test_find_number_positions(said, expected):
    assert find_number(said) == expected


@pytest.mark.parametrize(("said", "expected"), [
    ("零〇洞一幺二两三四五六七八九", "00011223456789"),
    ("二零二六一一一零杠一零", "20261110-10"),
    ("杠横杠横减-", "-----"),
    (" ２０２６\t十 A-1\n", "２０２６十A-1"),
])
def test_digits_to_ascii(said, expected):
    assert cn_digits_to_ascii(said) == expected
