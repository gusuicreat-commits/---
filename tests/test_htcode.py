import pytest

from app.htcode import check, normalize


@pytest.mark.parametrize("raw, expected", [
    ("20264827", None), ("20260007", None), ("20260000", None),
    ("00004827", "YEAR_INVALID"), ("202648270", "FORMAT"),
    (" ２０２６４８２７ ", None), ("20261110-10", None), ("20261110-15", "SEQ_INVALID"),
    ("20261131-10", "DATE_INVALID"), ("2026111-10", "DATE_LEN"),
    (" ２０２６１１１０－１０ ", None), ("abc", "FORMAT"),
    ("20261110-010", "SEQ_INVALID"), ("00001110-10", "DATE_INVALID"),
])
def test_check(raw, expected):
    error = check(normalize(raw))
    assert (error.code if error else None) == expected
