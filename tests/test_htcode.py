from datetime import date

import pytest

from app.htcode import check, normalize, suggest


@pytest.mark.parametrize("raw, expected", [
    ("20261110-10", None), ("20261110-15", "SEQ_INVALID"),
    ("20261131-10", "DATE_INVALID"), ("2026111-10", "DATE_LEN"),
    (" ２０２６１１１０－１０ ", None), ("abc", "FORMAT"),
    ("20261110-010", "SEQ_INVALID"), ("00001110-10", "DATE_INVALID"),
])
def test_check(raw, expected):
    error = check(normalize(raw))
    assert (error.code if error else None) == expected


@pytest.mark.parametrize("codes, expected", [
    ([], "20261110-10"), (["20261110-10", "20261110-20"], "20261110-30"),
    (["20261110-90"], None), (["20261109-90", "20261111-80"], "20261110-10"),
])
def test_suggest(codes, expected):
    assert suggest(codes, date(2026, 11, 10)) == expected
