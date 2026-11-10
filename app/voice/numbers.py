"""中文位值数字、逐位读数和合同号字符转换。"""

import re
from decimal import Decimal


_DIGITS = dict(zip("零〇一幺二两三四五六七八九", "0011223456789"))
_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_PATTERN = re.compile(r"[零〇一幺二两三四五六七八九十百千万点]+")
_ARABIC_PATTERN = re.compile(r"\d+(?:\.\d+)?")


def _parse_section(s: str) -> int | None:
    """解析万以内的一节；末尾单个非零数字可省略下一级单位。"""
    if not s:
        return 0
    if not any(c in _UNITS for c in s):
        return int("".join(_DIGITS[c] for c in s))
    total = 0
    pending = ""
    previous_unit = 10000
    for c in s:
        if c in _DIGITS:
            pending += _DIGITS[c]
            continue
        unit = _UNITS[c]
        if unit >= previous_unit:
            return None
        if pending:
            significant = pending.lstrip("0")
            if len(significant) != 1:
                return None
            digit = int(significant)
        elif unit == 10 and total == 0 and previous_unit == 10000:
            digit = 1
        else:
            return None
        total += digit * unit
        pending = ""
        previous_unit = unit
    if pending:
        tail = int(pending)
        if len(pending) == 1 and tail and previous_unit >= 100:
            tail *= previous_unit // 10
        if tail >= previous_unit:
            return None
        total += tail
    return total


def parse_cn_number(s: str) -> Decimal | None:
    """解析阿拉伯数字或中文数字；空串和不合法的位值结构返回空值。"""
    if _ARABIC_PATTERN.fullmatch(s):
        return Decimal(s)
    if not s or not _CN_PATTERN.fullmatch(s) or s.count("点") > 1:
        return None
    integer, separator, fraction = s.partition("点")
    if not integer or (separator and (not fraction or any(c not in _DIGITS for c in fraction))):
        return None
    if integer.count("万") > 1:
        return None
    if "万" in integer:
        high, low = integer.split("万")
        if not high:
            return None
        high_value = _parse_section(high)
        low_value = _parse_section(low)
        if high_value is None or not high_value or low_value is None:
            return None
        if len(low) == 1 and low in _DIGITS:
            low_value *= 1000
        value = high_value * 10000 + low_value
    else:
        value = _parse_section(integer)
    if value is None:
        return None
    decimal_part = "".join(_DIGITS[c] for c in fraction)
    return Decimal(f"{value}.{decimal_part}" if separator else str(value))


def find_number(s: str) -> tuple[Decimal, int, int] | None:
    """在两类数字中返回位置最靠前的有效读数及原文区间。"""
    candidates = []
    arabic = _ARABIC_PATTERN.search(s)
    if arabic:
        candidates.append((Decimal(arabic.group()), arabic.start(), arabic.end()))
    for match in _CN_PATTERN.finditer(s):
        value = parse_cn_number(match.group())
        if value is not None:
            candidates.append((value, match.start(), match.end()))
            break
    return min(candidates, key=lambda item: item[1]) if candidates else None


def cn_digits_to_ascii(s: str) -> str:
    """逐字转换合同号，不解释十百千万等位值单位。"""
    s = s.replace("横杠", "-")
    digits = {**_DIGITS, "洞": "0", "杠": "-", "横": "-", "减": "-"}
    return "".join(digits.get(c, c) for c in s if not c.isspace())
