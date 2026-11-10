"""合同号建议优先递增，达到上限后补最小空号。"""

from datetime import date

import pytest

from app.htcode import suggest


def test_suggest_smallest_gap_after_ninety():
    codes = [f"20261110-{number}" for number in (90, 80, 60, 50, 40, 30, 10)]
    assert suggest(codes, date(2026, 11, 10)) == "20261110-20"


def test_suggest_none_when_all_numbers_are_used():
    codes = [f"20261110-{number}" for number in range(10, 100, 10)]
    assert suggest(codes, date(2026, 11, 10)) is None


def test_suggest_increments_before_filling_gaps():
    assert suggest(["20261110-10", "20261110-50"], date(2026, 11, 10)) == "20261110-60"


@pytest.mark.parametrize("today_numbers, expected", [
    ([], "20261110-10"),
    ([10, 50], "20261110-60"),
    ([10, 30, 40, 50, 60, 80, 90], "20261110-20"),
])
def test_suggest_ignores_other_dates(today_numbers, expected):
    codes = [f"20261110-{number}" for number in today_numbers]
    codes += [f"{day}-{number}" for day in ("20261109", "20261111")
              for number in range(10, 100, 10)]
    assert suggest(codes, date(2026, 11, 10)) == expected


def test_suggest_ignores_invalid_codes_and_preserves_normalization():
    codes = ["20261110-10", "20261110-90", "20261110-020", "20261110-100",
             "20261110-15", "20261110-20-30", "20261131-20", "无效合同号",
             " ２０２６１１１０－３０ "]
    assert suggest(codes, date(2026, 11, 10)) == "20261110-20"


def test_suggest_counts_normalized_codes_as_used():
    codes = ["20261110-10", " ２０２６１１１０－２０ ", "20261110-90"]
    assert suggest(codes, date(2026, 11, 10)) == "20261110-30"
