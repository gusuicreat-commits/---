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

from .numbers import cn_digits_to_ascii, find_number, normalize_code, parse_cn_number


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
_ALLOWED_UNITS = {
    "length": {"毫米", "厘米", "公分", "mm", "cm", "米", "m"},
    "noise": {"分贝", "db"},
    "resistance": {"兆欧姆", "兆欧", "千欧姆", "千欧", "欧姆", "mohm", "kohm", "ohm", "mω", "kω", "ω"},
    "torque": {"牛米", "牛顿米", "牛顿·米", "牛顿・米", "nm", "n·m", "n・m", "n.m", "n*m"},
    "percent": {"%"},
}


def _remove_value_separators(text: str, fields: dict) -> tuple[str, set[int]]:
    """移除精确关键词内部、数字与允许单位之间的逗号，并记录分隔符序号。"""
    keywords = [keyword.lower() for definition in fields.values() for keyword in definition["keywords"]]
    units = set().union(*(_ALLOWED_UNITS.get(definition["type"], set()) for definition in fields.values()))
    removed = set()
    # 在去掉逗号的视图中定位边界，使一个关键词内的多个分隔符也能一并消除。
    compact = re.sub(r"\s*[,，、]\s*", "", text).lower()
    positions = []
    cursor = 0
    for match in re.finditer(r"\s*[,，、]\s*", text):
        cursor += len(text[positions[-1][1] if positions else 0:match.start()])
        positions.append((match.start(), match.end(), cursor))
    spans = []
    for start, end, position in positions:
        crosses_keyword = any(
            compact[max(0, position - split):position - split + len(keyword)] == keyword
            for keyword in keywords for split in range(1, len(keyword)) if position >= split
        )
        unit = _UNIT.match(text.lower(), end)
        joins_unit = (start > 0 and re.fullmatch(r"[\d零〇一幺二两三四五六七八九十百千万点.]", text[start - 1])
                      and unit and re.sub(r"\s+", "", unit[1]) in units)
        if crosses_keyword or joins_unit:
            spans.append((start, end))
    for index, match in enumerate(re.finditer(r"[，,。；;！!？?、\n]", text)):
        if any(start <= match.start() < end for start, end in spans):
            removed.add(index)
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text, removed


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


def _keyword_hits(clause: str, forms: dict, allow_empty: bool = False) -> list[dict]:
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
                    and not (allow_empty and not clause[match.end():].strip())
                    and not _NEGATIVE_PREFIX.search(clause[:match.start()])
                    and not _NEGATIVE_SUFFIX.match(clause[match.end():])):
                continue
            exact.append({"form": form, "field": field, "keyword": keyword,
                          "start": match.start(), "end": match.end(), "via": "exact"})

    for hit in exact:
        definition = forms[hit["form"]][hit["field"]]
        if definition.get("exact_only"):
            continue
        for keyword in sorted(definition["keywords"], key=len, reverse=True):
            if len(keyword) <= len(hit["keyword"]):
                continue
            tail = clause[hit["start"]:hit["start"] + len(keyword)]
            if len(tail) == len(keyword) and all(
                _pronunciations(char) & _pronunciations(expected)
                for char, expected in zip(tail, keyword)
            ):
                hit.update(keyword=keyword, end=hit["start"] + len(keyword), via="pinyin")
                break

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


def _unfinished_keyword_hit(clause: str, form: str, fields: dict, hits: list[dict]) -> dict | None:
    """逗号前只说了唯一字段的关键词前缀时，仍保留缺少数值的提示。"""
    candidates = []
    stripped = clause.rstrip()
    for field, definition in fields.items():
        for keyword in definition["keywords"]:
            for size in range(2, len(keyword)):
                if not stripped.lower().endswith(keyword[:size].lower()):
                    continue
                start = len(stripped) - size
                if any(start < hit["end"] and hit["start"] < len(clause) for hit in hits):
                    continue
                candidates.append({"form": form, "field": field, "keyword": keyword,
                                   "start": start, "end": len(clause), "via": "exact"})
    if not candidates:
        return None
    start = min(hit["start"] for hit in candidates)
    candidates = [hit for hit in candidates if hit["start"] == start]
    if len({hit["field"] for hit in candidates}) != 1:
        return None
    return max(candidates, key=lambda hit: len(hit["keyword"]))


def _numeric_suffix_hit(clause: str, form: str, fields: dict, hits: list[dict]) -> dict | None:
    """数值前省略了参数名前半段时，仅接受当前表单内归属唯一的精确后缀。"""
    candidates = []
    for field, definition in fields.items():
        if definition["type"] not in _ALLOWED_UNITS or definition.get("exact_only"):
            continue
        for keyword in definition["keywords"]:
            for start in range(1, len(keyword) - 1):
                suffix = keyword[start:]
                if not clause.lower().startswith(suffix.lower()):
                    continue
                if not _NUMBER_START.match(_VALUE_FILLERS.sub("", clause[len(suffix):])):
                    continue
                if any(hit["start"] < len(suffix) for hit in hits):
                    continue
                candidates.append({"form": form, "field": field, "keyword": keyword,
                                   "start": 0, "end": len(suffix), "via": "exact"})
    if len({hit["field"] for hit in candidates}) == 1:
        return max(candidates, key=lambda hit: hit["end"])
    return None


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
        if definition.get("code"):
            s = normalize_code(s)
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
    if unit is not None and unit not in _ALLOWED_UNITS[kind]:
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
    corrected, removed_separators = _remove_value_separators(corrected, config["forms"][form])
    result = {"text": text, "corrected": corrected, "fields": {}, "notes": {},
              "matched": [], "rejected": [], "message": None}
    if form == 'travel_paint':
        for match in re.finditer(r'(?:底气|第七|面气|面旗|勉其)(?=[\s,，、]*[\d零〇一幺二两三四五六七八九十])', corrected):
            result['rejected'].append({'field': 'paint_kind', 'said': match.group(),
                                       'reason': '漆种名称不明确，请核对是底漆还是面漆'})
    last_field = None
    last_keyword = ""
    retract_pending = False
    pending_field = None

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
    separator_index = 0
    for char in text:
        if re.fullmatch(clause_pattern, unicodedata.normalize("NFKC", char)):
            if separator_index not in removed_separators:
                original_clauses.append("")
            separator_index += 1
        else:
            original_clauses[-1] += char
    # 自定义纠错若增删了分隔符，改用处理后的子句，仍保证否定提示逐段对应。
    clauses = re.split(clause_pattern, corrected)
    if len(original_clauses) != len(clauses):
        original_clauses = clauses
    replacements = []
    clause_offset = 0
    for clause_index, clause in enumerate(clauses):
        offset = clause_offset
        clause_offset += len(clause) + 1
        if not clause.strip():
            continue
        retracts = [word for word in config["retract_words"]
                    if re.search(re.escape(word), clause, flags=re.IGNORECASE)]
        if retracts:
            retract_pending = True
        separator = corrected[offset + len(clause):offset + len(clause) + 1]
        hits = _keyword_hits(clause, config["forms"], allow_empty=separator in {',', '，', '、'})
        suffix_hit = _numeric_suffix_hit(clause, form, config["forms"][form], hits)
        if suffix_hit:
            hits = [suffix_hit] + hits
        if separator in {',', '，', '、'}:
            unfinished = _unfinished_keyword_hit(clause, form, config["forms"][form], hits)
            if unfinished:
                hits = sorted(hits + [unfinished], key=lambda hit: hit["start"])
        consumed_pending = False
        if pending_field:
            field, keyword, via = pending_field
            pending_field = None
            prefix = clause[:hits[0]["start"]] if hits else clause
            said = _VALUE_FILLERS.sub("", prefix).strip()
            if _NUMBER_START.match(said):
                if assign(field, keyword, said, via):
                    last_field, last_keyword = field, keyword
            else:
                result['rejected'].append({'field': field, 'said': keyword, 'reason': '没有说出数值'})
            consumed_pending = True
        if hits:
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
                if not said and end == len(clause) and separator in {',', '，', '、'}:
                    pending_field = (hit['field'], hit['keyword'], hit['via'])
                    continue  # 参数名后的逗号可由 ASR 插入，值属于紧邻下一片段。
                if assign(hit["field"], hit["keyword"], said, hit["via"]):
                    last_field, last_keyword = hit["field"], hit["keyword"]
            retract_pending = False
        elif consumed_pending:
            retract_pending = False
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
    if pending_field:
        result['rejected'].append({'field': pending_field[0], 'said': pending_field[1], 'reason': '没有说出数值'})
    if not result["fields"]:
        result["message"] = "未识别到参数"
    return result
