from dataclasses import dataclass
from datetime import date
import re
import unicodedata


@dataclass(frozen=True)
class HtCodeError:
    code: str
    message: str


def normalize(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw.strip())


def check(code: str) -> HtCodeError | None:
    if not re.fullmatch(r"\d+-\d+", code, flags=re.ASCII):
        return HtCodeError("FORMAT", "合同号格式应为「日期-序号」，例如 20261110-10")
    day, seq = code.split("-")
    if len(day) != 8:
        return HtCodeError("DATE_LEN", f"日期部分应为 8 位数字，当前是 {len(day)} 位")
    try:
        date(int(day[:4]), int(day[4:6]), int(day[6:]))
    except ValueError:
        return HtCodeError("DATE_INVALID", f"日期 {day} 不存在")
    if seq not in {str(n) for n in range(10, 100, 10)}:
        return HtCodeError("SEQ_INVALID", f"序号应为 10、20、…、90 之一，当前是 {seq}")
    return None


def suggest(existing_codes: list[str], today: date) -> str | None:
    prefix = today.strftime("%Y%m%d")
    sequences = {int(code.split("-")[1]) for raw in existing_codes
                 if check(code := normalize(raw)) is None and code.split("-")[0] == prefix}
    seq = max(sequences, default=0) + 10
    if seq > 90:
        seq = next((number for number in range(10, 100, 10) if number not in sequences), None)
    return f"{prefix}-{seq}" if seq is not None else None
