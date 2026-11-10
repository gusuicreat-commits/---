from datetime import date, datetime
from zoneinfo import ZoneInfo


def now_bj() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def today_bj() -> date:
    return now_bj().date()
