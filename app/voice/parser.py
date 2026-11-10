"""将识别文字解析成表单字段，不连接识别引擎或数据库。"""

import json
import os
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, pinyin

from .numbers import cn_digits_to_ascii, find_number, parse_cn_number


@lru_cache(maxsize=1)
def load_voice_config() -> dict:
    """读取配置；环境变量可指定配置文件，相对路径以仓库根目录为基准。"""
    root = Path(__file__).resolve().parents[2]
    path = Path(os.environ.get("VOICE_CONFIG_PATH", "config/voice.json"))
    if not path.is_absolute():
        path = root / path
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def hotwords() -> list[str]:
    """返回热词副本，避免调用方修改缓存配置。"""
    return list(load_voice_config()["hotwords"])


_NUMBER_START = re.compile(r"[\d零〇一幺二两三四五六七八九十百千万点]")
_UNIT = re.compile(r"\s*(毫米|厘米|公分|分贝|mm|cm|db|米|m)")
_CODE = re.compile(r"(\d{8})\s*[-]?\s*(\d{2})(?!\d)")
_DATE_DIGITS = r"[\d零〇一幺二两三四五六七八九十百千万]+"
_VALUE_FILLERS = re.compile(r"^(?:\s|等于|是|为|:|：|大概|大约|约|差不多|在)+")
_SPOKEN_DIGITS = re.compile(r"[0-9零〇一幺二两三四五六七八九]+")


@lru_cache(maxsize=4096)
def _pronunciations(ch: str) -> frozenset[str]:
    """按字缓存全部无声调读音，保留多音字的匹配机会。"""
    return frozenset(pinyin(ch, heteronym=True, style=Style.NORMAL)[0])


def _keyword_hits(clause: str, forms: dict) -> list[dict]:
    """先选不重叠的精确关键词，再在剩余窗口中查找同音词。"""
    definitions = [
        (form, field, keyword)
        for form, fields in forms.items()
        for field, definition in fields.items()
        for keyword in definition["keywords"]
    ]
    exact = []
    exact_keywords = set()
    for form, field, keyword in definitions:
        for match in re.finditer(re.escape(keyword), clause):
            if len(keyword) == 1 and not _NUMBER_START.match(clause, match.end()):
                continue
            exact_keywords.add(keyword)
            exact.append({"form": form, "field": field, "keyword": keyword,
                          "start": match.start(), "end": match.end(), "via": "exact"})

    def select(candidates: list[dict], occupied: list[dict]) -> list[dict]:
        selected = []
        for hit in sorted(candidates, key=lambda item: (item["start"], -len(item["keyword"]))):
            if any(hit["start"] < other["end"] and other["start"] < hit["end"]
                   for other in occupied + selected):
                continue
            selected.append(hit)
        return selected

    hits = select(exact, [])
    fuzzy = []
    for form, field, keyword in definitions:
        if len(keyword) < 2 or keyword in exact_keywords:
            continue
        pronunciations = [_pronunciations(ch) for ch in keyword]
        for start in range(len(clause) - len(keyword) + 1):
            end = start + len(keyword)
            if any(start < hit["end"] and hit["start"] < end for hit in hits):
                continue
            window = clause[start:end]
            if all(_pronunciations(ch) & sounds
                   for ch, sounds in zip(window, pronunciations)):
                fuzzy.append({"form": form, "field": field, "keyword": keyword,
                              "start": start, "end": end, "via": "pinyin"})
    return sorted(hits + select(fuzzy, hits), key=lambda item: item["start"])


def _clean_value(s: str) -> str:
    return re.sub(r"^(?:\s|等于|是|为|:|：)+", "", s).strip()


def _check_ht_code(value: str) -> bool:
    """检查合同日期真实有效，序号为十至九十的整十数。"""
    if not re.fullmatch(r"\d{8}-\d{2}", value):
        return False
    try:
        date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return False
    return int(value[-2:]) in range(10, 91, 10)


def _parse_ht_code(s: str, today: date) -> tuple[str | None, str | None]:
    match = _CODE.search(s)
    if match is None:
        match = _CODE.search(cn_digits_to_ascii(s))
    if match is None:
        # 中文整十序号先按位值解释，再逐字转换剩余的合同日期。
        converted = re.sub(
            r"[一幺二两三四五六七八九]?十",
            lambda item: str(parse_cn_number(item.group())), s,
        )
        match = _CODE.search(cn_digits_to_ascii(converted))
    if match:
        value = f"{match[1]}-{match[2]}"
        return (value, None) if _check_ht_code(value) else (None, None)
    serial = parse_cn_number(s)
    if serial is not None and serial in range(10, 91, 10):
        value = f"{today:%Y%m%d}-{int(serial):02d}"
        if _check_ht_code(value):
            return value, "已补当天日期"
    return None, None


def _parse_date(s: str, today: date) -> str | None:
    relative = re.search(r"今天|昨天|前天", s)
    if relative:
        try:
            return (today - timedelta(days={"今天": 0, "昨天": 1, "前天": 2}[relative.group()])).isoformat()
        except OverflowError:
            return None
    iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", s)
    chinese = re.search(
        rf"(?:({_DATE_DIGITS})年)?({_DATE_DIGITS})月({_DATE_DIGITS})[日号]", s,
    )
    try:
        if iso:
            return date(*(int(part) for part in iso.groups())).isoformat()
        if chinese:
            year, month, day = chinese.groups()
            parts = [parse_cn_number(part) if part else Decimal(today.year)
                     for part in (year, month, day)]
            if any(part is None for part in parts):
                return None
            return date(*(int(part) for part in parts)).isoformat()
    except (ValueError, OverflowError):
        return None
    return None


def _parse_value(s: str, field: str, definition: dict, keyword: str, today: date
                 ) -> tuple[str | None, str | None, str | None]:
    """返回字段值、换算提示和拒绝原因。"""
    kind = definition["type"]
    if kind == "text":
        strict = definition.get("strict_keywords")
        if not s or (strict and keyword not in strict and not re.search(r"[a-z0-9]", s)):
            return None, None, "没有内容"
        return s[:128 if field == "name" else 255], None, None
    if not s:
        return None, None, "没有说出数值"
    if kind == "ht_code":
        value, note = _parse_ht_code(s, today)
        return value, note, None if value else "合同号格式不对"
    if kind == "date":
        value = _parse_date(s, today)
        return value, None, None if value else "没有说出数值"

    s = _VALUE_FILLERS.sub("", s)
    number = find_number(s)
    if number is None or number[1] != 0:
        return None, None, "没有说出数值"
    value, _, end = number
    unit_match = _UNIT.match(s, end)
    unit = unit_match[1] if unit_match else None
    # 无单位时必须是完整读数，避免把普通词语里的数字当成测量结果。
    if unit_match is None and s[end:].strip():
        return None, None, "没有说出数值"
    length_units = {"毫米", "厘米", "公分", "mm", "cm", "米", "m"}
    if ((kind == "length" and unit in {"分贝", "db"})
            or (kind == "noise" and unit in length_units)):
        return None, None, "单位不对"
    factor, note = 1, None
    if kind == "length":
        if unit in {"厘米", "公分", "cm"}:
            factor, note = 10, "已从厘米换算"
        elif unit in {"米", "m"}:
            factor, note = 1000, "已从米换算"
            fraction = _SPOKEN_DIGITS.match(s, unit_match.end())
            if fraction:
                if (value != value.to_integral_value()
                        or s[fraction.end():fraction.end() + 1] in {"十", "百", "千", "万", "点", "."}):
                    return None, None, "数值说法无法确定"
                digits = cn_digits_to_ascii(fraction.group())
                value = Decimal(f"{int(value)}.{digits}")
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 8)
        value = (value * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(value, "f").rstrip("0").rstrip("."), note, None


def parse_text(text: str, form: str, today: date) -> dict:
    """按表单解析；日期由调用方以北京时间传入，避免依赖机器本地时区。"""
    config = load_voice_config()
    if form not in {"contract", "drum", "travel_drive"}:
        raise ValueError("不支持的表单类型")
    corrected = text
    for wrong, right in sorted(config["corrections"].items(), key=lambda item: -len(item[0])):
        corrected = corrected.replace(wrong, right)
    corrected = unicodedata.normalize("NFKC", corrected).lower()
    result = {"text": text, "corrected": corrected, "fields": {}, "notes": {},
              "matched": [], "rejected": [], "message": None}
    last_field = None
    last_keyword = ""
    retract_pending = False

    def assign(field: str, keyword: str, said: str, via: str) -> bool:
        value, note, reason = _parse_value(said, field, config["forms"][form][field], keyword, today)
        if value is None:
            result["rejected"].append({"field": field, "said": said, "reason": reason})
            return False
        result["fields"][field] = value
        result["notes"].pop(field, None)
        if note:
            result["notes"][field] = note
        result["matched"].append({"field": field, "keyword": keyword, "via": via, "value": value})
        return True

    for clause in re.split(r"[，,。；;！!？?、\n]", corrected):
        if not clause.strip():
            continue
        retracts = [word for word in config["retract_words"] if word in clause]
        if retracts:
            retract_pending = True
        hits = _keyword_hits(clause, config["forms"])
        if hits:
            for index, hit in enumerate(hits):
                if hit["form"] != form:
                    continue
                end = hits[index + 1]["start"] if index + 1 < len(hits) else len(clause)
                said = _clean_value(clause[hit["end"]:end])
                if assign(hit["field"], hit["keyword"], said, hit["via"]):
                    last_field, last_keyword = hit["field"], hit["keyword"]
            retract_pending = False
        elif retract_pending and last_field and find_number(clause) is not None:
            said = clause
            for word in sorted(config["retract_words"], key=len, reverse=True):
                said = said.replace(word, "")
            assign(last_field, last_keyword, _clean_value(said), "retract")
            retract_pending = False
        elif not retracts:
            retract_pending = False
    if not result["fields"]:
        result["message"] = "未识别到参数"
    return result
