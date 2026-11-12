"""按逐字时间戳校正缺少真实停顿的逗号。"""

import re


PUNCT = "，。？！、,.?!；;"
COMMAS = "，,、"
NUMERALS = "零〇一幺二两三四五六七八九十百千万点0123456789"
TOKEN = re.compile(r"[A-Za-z0-9]+|[，。？！、,.?!；;]|[^\sA-Za-z0-9]")


def gate_commas(text: str, timestamps: list | None, min_gap_ms: int) -> tuple[str, dict]:
    tokens = TOKEN.findall(text)
    spoken = [token for token in tokens if token not in PUNCT]
    commas = sum(token in COMMAS for token in tokens)
    if not timestamps or len(spoken) != len(timestamps):
        return text, {"aligned": False, "commas": commas, "removed": 0}

    output = []
    position = 0
    removed = 0
    previous_char = ""
    for token in tokens:
        if token not in PUNCT:
            position += 1
        elif token in COMMAS and 0 < position < len(spoken):
            gap = timestamps[position][0] - timestamps[position - 1][1]
            separates_numbers = previous_char in NUMERALS and spoken[position][0] in NUMERALS
            if gap < min_gap_ms and not separates_numbers:
                output.append("")
                removed += 1
                continue
        output.append(token)
        previous_char = token[-1]

    # 只替换切分出的 token，保留原文中的空白与英文词间距。
    replacements = iter(output)
    corrected = TOKEN.sub(lambda match: next(replacements), text)
    return corrected, {
        "aligned": True, "commas": commas, "removed": removed, "min_gap_ms": min_gap_ms,
    }
