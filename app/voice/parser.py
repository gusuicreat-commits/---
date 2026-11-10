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

from app import htcode
from app.dec import fmt
from app.readings import normalize_readings

from .numbers import cn_digits_to_ascii, find_number, parse_cn_number


_learned_corrections: dict[str, str] = {}


def set_learned_corrections(mapping: dict[str, str]) -> None:
    global _learned_corrections
    _learned_corrections = dict(mapping)


def learned_corrections() -> dict[str, str]:
    return dict(_learned_corrections)


def corrections() -> dict[str, str]:
    return {**load_voice_config()["corrections"], **_learned_corrections}


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
_UNIT = re.compile(
    r"\s*(兆欧姆|兆欧|千欧姆|千欧|欧姆|牛顿[·・\s]*米|牛米|"
    r"mohm|kohm|ohm|mω|kω|ω|n\s*[·・.*]?\s*m|"
    r"毫米|厘米|公分|分贝|mm|cm|d\s*b|米|m|%)"
)
_CODE = re.compile(r"(?<!\d)(\d{8})\s*[-]?\s*(\d{2})(?!\d)")
_YEAR_CODE = re.compile(r"(?<![\d.-])([0-9]{4})\s*([0-9]{4})(?![\d\s.-]*\d)")
_DATE_DIGITS = r"[\d零〇一幺二两三四五六七八九十百千万]+"
_VALUE_FILLERS = re.compile(
    r"^(?:\s|等于|是|为|:|：|大概齐|大约摸|大概|大约|约莫|约|差不多|差不离|在|"
    r"得有|能有|有|估摸着|估摸|得|整)+"
)
_VALUE_PARTICLES = re.compile(r"[嘞哩咧唻啦呢啊呀哈嘛了吧哟]+")
_NEGATIVE_PREFIX = re.compile(r"(?:没有|木有|冇|没)\s*(?:测|量)?\s*$")
_NEGATIVE_SUFFIX = re.compile(r"^\s*(?:还没测|没测|没量)")
_SPOKEN_DIGITS = re.compile(r"[0-9零〇一幺二两三四五六七八九]+")
_UNCERTAIN_NUMBER = re.compile(r"[\d零〇一幺二两三四五六七八九十百千万点.]+[几多来]")


@lru_cache(maxsize=4096)
def _pronunciations(ch: str) -> frozenset[str]:
    """按字缓存全部无声调读音，保留多音字的匹配机会。"""
    return frozenset(pinyin(ch, heteronym=True, style=Style.NORMAL)[0])


@lru_cache(maxsize=4096)
def _dialect_pronunciations(ch: str) -> frozenset[str]:
    """只合并指定声母和完整韵母，避免把 iang 等韵母也截成 an。"""
    sounds = set()
    for sound in _pronunciations(ch):
        match = re.fullmatch(r"(zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw]?)([a-z]+)", sound)
        if match is None:
            continue
        initial, final = match.groups()
        initial = {"zh": "z", "ch": "c", "sh": "s", "n": "l"}.get(initial, initial)
        final = {"ang": "an", "eng": "en", "ing": "in"}.get(final, final)
        sounds.add(initial + final)
    return frozenset(sounds)


def _keyword_hits(clause: str, forms: dict) -> list[dict]:
    """依次选取精确、普通同音、口音模糊命中，后者不得覆盖前者。"""
    definitions = [
        (form, field, keyword)
        for form, fields in forms.items()
        for field, definition in fields.items()
        for keyword in definition["keywords"]
    ]
    exact = []
    for form, field, keyword in definitions:
        for match in re.finditer(re.escape(keyword), clause, flags=re.IGNORECASE):
            # 分贝/dB 也能作为字段名，但数值后的单位不能再切出一个空字段。
            if field == "noise" and keyword.lower() in {"分贝", "db"}:
                tail = _VALUE_FILLERS.sub("", clause[match.end():])
                if not _NUMBER_START.match(tail):
                    continue
                prefix = clause[:match.start()]
                if re.search(r"(?:噪声|噪音)(?:\s|是|为|:|：)*$", prefix):
                    continue
            if (len(keyword) == 1 and not _NUMBER_START.match(clause, match.end())
                    and not _NEGATIVE_PREFIX.search(clause[:match.start()])
                    and not _NEGATIVE_SUFFIX.match(clause[match.end():])):
                continue
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
    uncertain_numbers = list(_UNCERTAIN_NUMBER.finditer(clause))
    for via, pronounce in (("pinyin", _pronunciations), ("fuzzy", _dialect_pronunciations)):
        candidates = []
        for form, field, keyword in definitions:
            if forms[form][field].get('exact_only'):
                continue  # 底漆/面漆须有明确文字，不用同音或口音匹配替代。
            if field == "noise" and keyword.lower() in {"分贝", "db"}:
                continue  # 单位别名只精确识别，避免把数值尾部单位重新当作字段。
            if len(keyword) < 2:
                continue
            pronunciations = [pronounce(ch) for ch in keyword]
            for start in range(len(clause) - len(keyword) + 1):
                end = start + len(keyword)
                # 「一点几」中的「点几」不能被同音匹配为「电机」，截断数值。
                if any(start < number.end() and number.start() < end for number in uncertain_numbers):
                    continue
                if any(start < hit["end"] and hit["start"] < end for hit in hits):
                    continue
                if not all(pronounce(ch) & sounds
                           for ch, sounds in zip(clause[start:end], pronunciations)):
                    continue
                if via == "fuzzy":
                    number = find_number(_VALUE_FILLERS.sub("", clause[end:]))
                    if number is None or number[1] != 0:
                        continue
                candidates.append({"form": form, "field": field, "keyword": keyword,
                                   "start": start, "end": end, "via": via})
        hits += select(candidates, hits)
    return sorted(hits, key=lambda item: item["start"])


def _clean_value(s: str) -> str:
    return re.sub(r"^(?:\s|等于|是|为|:|：)+", "", s).strip()


def _check_ht_code(value: str) -> bool:
    """检查新编号，同时兼容完整的历史合同号。"""
    return htcode.check(value) is None


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
    # 新编号必须说全八位；不能再用「二十」补出当天日期的旧编号。
    match = _YEAR_CODE.search(cn_digits_to_ascii(s))
    if match:
        value = match[1] + match[2]
        if _check_ht_code(value):
            return value, None
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
    if kind == 'number_list':
        try:
            return normalize_readings(s.rstrip().removesuffix('.')), None, None
        except ValueError as exc:
            return None, None, str(exc)
    if kind == "text":
        strict = definition.get("strict_keywords")
        if not s or (strict and keyword not in strict
                     and not re.search(r"[a-z0-9]", s, flags=re.IGNORECASE)):
            return None, None, "没有内容"
        return s[:128 if field == "name" else 255], None, None
    # 仅对非文本字段统一大小写，沿用合同号、日期和数值的解析行为。
    s = s.lower()
    if not s:
        return None, None, "没有说出数值"
    if kind == "ht_code":
        value, note = _parse_ht_code(s, today)
        return value, note, None if value else "合同号格式不对"
    if kind == "date":
        value = _parse_date(s, today)
        return value, None, None if value else "没有说出数值"

    # ASR 可能输出英文句号：仅移除数值片段末尾一个句号，保留小数点与 N.m 等单位。
    s = s.rstrip().removesuffix(".").rstrip()
    s = _VALUE_FILLERS.sub("", s)
    if kind == "noise":
        s = re.sub(r"^(?:分贝|d\s*b)\s*", "", s)
        s = _VALUE_FILLERS.sub("", s)
    if kind == "percent":
        s = re.sub(r"^百分之\s*", "", s)
    if _UNCERTAIN_NUMBER.match(s):
        return None, None, "数值说法无法确定"
    number = find_number(s)
    if number is None or number[1] != 0:
        return None, None, "没有说出数值"
    value, _, end = number
    unit_match = _UNIT.match(s, end)
    unit = unit_match[1] if unit_match else None
    unit = re.sub(r"\s+", "", unit) if unit else None
    value_end = unit_match.end() if unit_match else end
    length_units = {"毫米", "厘米", "公分", "mm", "cm", "米", "m"}
    allowed_units = {
        "length": length_units, "noise": {"分贝", "db"},
        "resistance": {"兆欧姆", "兆欧", "千欧姆", "千欧", "欧姆", "mohm", "kohm", "ohm", "mω", "kω", "ω"},
        "torque": {"牛米", "牛顿米", "牛顿·米", "牛顿・米", "nm", "n·m", "n・m", "n.m", "n*m"},
        "percent": {"%"},
    }
    if unit is not None and unit not in allowed_units[kind]:
        return None, None, "单位不对"
    factor, note = 1, None
    if kind == "resistance" and unit in {"千欧姆", "千欧", "kohm", "kω"}:
        factor, note = Decimal("0.001"), "已从千欧换算为兆欧"
    elif kind == "resistance" and unit in {"欧姆", "ohm", "ω"}:
        factor, note = Decimal("0.000001"), "已从欧姆换算为兆欧"
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
                value_end = fraction.end()
    # 只容许完整读数后接语气词，既不截断数字，也不跳过普通文字。
    tail = s[value_end:].strip()
    if tail and not _VALUE_PARTICLES.fullmatch(tail):
        return None, None, "没有说出数值"
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 8)
        value = (value * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return fmt(value), note, None


def parse_text(text: str, form: str, today: date) -> dict:
    """按表单解析；日期由调用方以北京时间传入，避免依赖机器本地时区。"""
    config = load_voice_config()
    if form not in config["forms"]:
        raise ValueError("不支持的表单类型")
    corrected = unicodedata.normalize("NFKC", text)
    for wrong, right in sorted(corrections().items(), key=lambda item: -len(item[0])):
        def replace(match):
            if form == 'travel_paint':
                protected = list(re.finditer('底漆|面漆', corrected))
                if any(match.start() < p.end() and p.start() < match.end() for p in protected):
                    candidate = corrected[:match.start()] + right + corrected[match.end():]
                    if re.findall('底漆|面漆', candidate) != [p.group() for p in protected]:
                        return match.group()  # 包括短字纠错，均不得改掉已明确的漆种。
            return right
        corrected = re.sub(re.escape(unicodedata.normalize("NFKC", wrong)),
                           replace, corrected, flags=re.IGNORECASE)
    result = {"text": text, "corrected": corrected, "fields": {}, "notes": {},
              "matched": [], "rejected": [], "message": None}
    if form == 'travel_paint':
        for match in re.finditer(r'(?:底气|第七|面气|面旗|勉其)(?=[\s,，、]*[\d零〇一幺二两三四五六七八九十])', corrected):
            result['rejected'].append({'field': 'paint_kind', 'said': match.group(),
                                       'reason': '漆种名称不明确，请核对是底漆还是面漆'})
    last_field = None
    last_keyword = ""
    retract_pending = False
    pending_paint = None

    def assign(field: str, keyword: str, said: str, via: str) -> bool:
        value, note, reason = _parse_value(said, field, config["forms"][form][field], keyword, today)
        if value is None:
            if config['forms'][form][field]['type'] == 'number_list':
                result['fields'].pop(field, None)
            result["rejected"].append({"field": field, "said": said, "reason": reason})
            return False
        result["fields"][field] = value
        result["notes"].pop(field, None)
        if note:
            result["notes"][field] = note
        matched = {"field": field, "keyword": keyword, "via": via, "value": value}
        if via == "fuzzy":
            matched["fuzzy"] = True
        result["matched"].append(matched)
        return True

    clause_pattern = r"[，,。；;！!？?、\n]"
    # 按规范化后的分隔符切分原文，保留否定子句里的原字、空格和全角数字。
    original_clauses = [""]
    for char in text:
        if re.fullmatch(clause_pattern, unicodedata.normalize("NFKC", char)):
            original_clauses.append("")
        else:
            original_clauses[-1] += char
    replacements = []
    clause_offset = 0
    for clause_index, clause in enumerate(re.split(clause_pattern, corrected)):
        offset = clause_offset
        clause_offset += len(clause) + 1
        if not clause.strip():
            continue
        retracts = [word for word in config["retract_words"]
                    if re.search(re.escape(word), clause, flags=re.IGNORECASE)]
        if retracts:
            retract_pending = True
        hits = _keyword_hits(clause, config["forms"])
        if hits:
            if pending_paint:
                result['rejected'].append({'field': pending_paint[0], 'said': pending_paint[1], 'reason': '没有说出数值'})
                pending_paint = None
            for index, hit in enumerate(hits):
                if hit["form"] != form:
                    continue
                end = hits[index + 1]["start"] if index + 1 < len(hits) else len(clause)
                start = hits[index - 1]["end"] if index else 0
                if (_NEGATIVE_PREFIX.search(clause[start:hit["start"]])
                        or _NEGATIVE_SUFFIX.match(clause[hit["end"]:end])):
                    result["rejected"].append({
                        "field": hit["field"], "said": original_clauses[clause_index],
                        "reason": "说还没测",
                    })
                    continue
                if hit["via"] == "fuzzy":
                    replacements.append((offset + hit["start"], offset + hit["end"], hit["keyword"]))
                said = _clean_value(clause[hit["end"]:end])
                separator = corrected[offset + len(clause):offset + len(clause) + 1]
                if (form == 'travel_paint' and not said and hit['field'] in
                        {'primer_thickness_ratio', 'total_thickness_ratio'} and separator in {',', '，', '、'}):
                    pending_paint = (hit['field'], hit['keyword'])
                    continue  # ASR 可在“面漆，九十九”中补逗号，值属于紧邻下一片段。
                if assign(hit["field"], hit["keyword"], said, hit["via"]):
                    last_field, last_keyword = hit["field"], hit["keyword"]
            retract_pending = False
        elif pending_paint:
            field, keyword = pending_paint
            pending_paint = None
            if assign(field, keyword, _clean_value(clause), 'exact'):
                last_field, last_keyword = field, keyword
        elif retract_pending and last_field and find_number(clause) is not None:
            said = clause
            for word in sorted(config["retract_words"], key=len, reverse=True):
                said = re.sub(re.escape(word), "", said, flags=re.IGNORECASE)
            assign(last_field, last_keyword, _clean_value(said), "retract")
            retract_pending = False
        elif (not retracts and last_field and config['forms'][form][last_field]['type'] == 'number_list'
              and offset > 0 and corrected[offset - 1] in ',，、;；\n'):
            previous = result['fields'].get(last_field)
            if assign(last_field, last_keyword, _clean_value(clause), 'exact'):
                if previous:
                    result['fields'][last_field] = previous + ',' + result['fields'][last_field]
            else:
                last_field = None
        elif not retracts:
            retract_pending = False
    for start, end, keyword in reversed(replacements):
        result["corrected"] = result["corrected"][:start] + keyword + result["corrected"][end:]
    if pending_paint:
        result['rejected'].append({'field': pending_paint[0], 'said': pending_paint[1], 'reason': '没有说出数值'})
    if not result["fields"]:
        result["message"] = "未识别到参数"
    return result
