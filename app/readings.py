"""多测点读数：严格解析完整输入，避免漏掉不确定或无效测点。"""
from decimal import Decimal
import re
import unicodedata
from app.dec import fmt, to_dec2
from app.voice.numbers import parse_cn_number
from functools import lru_cache


def split_spoken_tens(text):
    """ASR 漏标点时，仅拆分具有唯一划分的连续整十/两位中文读数。"""
    @lru_cache(None)
    def split(start):
        if start == len(text):
            return [()]
        solutions = []
        for width in (1, 2, 3):
            word = text[start:start + width]
            if len(word) != width or not re.fullmatch('[一二两三四五六七八九]?十[一二两三四五六七八九]?', word):
                continue
            for tail in split(start + width):
                solutions.append((parse_cn_number(word),) + tail)
                if len(solutions) > 1:
                    return solutions
        return solutions
    solutions = split(0)
    return solutions[0] if len(solutions) == 1 and len(solutions[0]) > 1 else None


def parse_readings(raw):
    if raw is None or not str(raw).strip():
        return []
    text = unicodedata.normalize('NFKC', str(raw)).strip()
    parts = re.split(r'[,，、;；\s]+', text)
    result = []
    for i, part in enumerate(parts, 1):
        part = re.sub(r'^(?:百分之|是|为|约)', '', part)
        part = re.sub(r'(?:微米|μm|um|%)$', '', part, flags=re.I)
        value = Decimal(part) if re.fullmatch(r'-?\d+(?:\.\d+)?', part) else parse_cn_number(part)
        if value is None:
            spoken = split_spoken_tens(part)
            if spoken:
                result.extend(to_dec2(v) for v in spoken)
                continue
            raise ValueError(f'第{i}个测点「{part}」不是明确数字')
        try:
            result.append(to_dec2(value))
        except Exception:
            raise ValueError(f'第{i}个测点最多两位小数') from None
    return result


def normalize_readings(raw):
    return ','.join(fmt(value) for value in parse_readings(raw)) or None
