from dataclasses import dataclass
from datetime import date
import re
import secrets
import unicodedata


@dataclass(frozen=True)
class HtCodeError:
    code: str
    message: str


def normalize(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw.strip())


def check(code: str) -> HtCodeError | None:
    """接受年份＋四位数字；兼容历史日期-序号合同及其导出。"""
    if re.fullmatch(r"[0-9]{8}", code):
        if int(code[:4]) == 0:
            return HtCodeError("YEAR_INVALID", "合同号前四位应为有效年份")
        return None
    if not re.fullmatch(r"\d+-\d+", code, flags=re.ASCII):
        return HtCodeError("FORMAT", "合同号格式应为「4位年份＋4位数字」，例如 20264827")
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
    prefix = f"{today.year:04d}"
    used = {normalize(code) for code in existing_codes}
    # 从剩余号码中随机取一个，避免重复重试在接近用满时误报无号。
    available = [f"{prefix}{number:04d}" for number in range(10000)
                 if f"{prefix}{number:04d}" not in used]
    return secrets.choice(available) if available else None
