"""随机合同号避重、前导零、年份切换和完整号码空间。"""

from datetime import date

from app import htcode


TODAY = date(2026, 11, 10)


def test_random_choice_is_from_available_numbers(monkeypatch):
    seen = []

    def choose(pool):
        seen.extend(pool)
        return pool[-1]

    monkeypatch.setattr(htcode.secrets, "choice", choose)
    codes = ["20260000", " ２０２６９９９９ ", "20261110-10", "20259998"]
    assert htcode.suggest(codes, TODAY) == "20269998"
    assert len(seen) == 9998
    assert "20260000" not in seen and "20269999" not in seen
    assert seen[0] == "20260001"


def test_can_allocate_more_than_nine_and_ignores_month_day():
    codes = [f"20261110-{n}" for n in range(10, 100, 10)]
    for _ in range(30):
        code = htcode.suggest(codes, TODAY)
        assert len(code) == 8 and code.startswith("2026") and code not in codes
        codes.append(code)


def test_last_available_number_even_with_leading_zero():
    codes = [f"2026{n:04d}" for n in range(10000) if n != 7]
    assert htcode.suggest(codes, TODAY) == "20260007"
    assert htcode.suggest(codes + ["20260007"], TODAY) is None


def test_each_year_has_its_own_namespace():
    codes = [f"2026{n:04d}" for n in range(10000)]
    code = htcode.suggest(codes, date(2027, 1, 1))
    assert code.startswith("2027") and len(code) == 8


def test_randomness_is_used_on_each_suggestion(monkeypatch):
    choices = iter(["20264827", "20260000"])
    monkeypatch.setattr(htcode.secrets, "choice", lambda pool: next(choices))
    assert htcode.suggest([], TODAY) == "20264827"
    assert htcode.suggest([], date(2026, 12, 31)) == "20260000"
