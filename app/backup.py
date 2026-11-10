import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.timeutil import today_bj


def _day(value=None) -> date:
    if value is None:
        return today_bj()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return value.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return value


def backup_now(now=None) -> Path:
    settings = get_settings()
    day = _day(now)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backup_dir / f"qc-{day:%Y%m%d}.sqlite3"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=settings.backup_dir, delete=False) as stream:
            temporary = Path(stream.name)
        # 只读打开源文件，缺失时不能悄悄创建一个空数据库。
        source_uri = settings.db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def prune(keep_days: int, today=None) -> list[Path]:
    if keep_days < 0:
        raise ValueError("备份保留天数不能为负数")
    cutoff = _day(today) - timedelta(days=keep_days)
    removed = []
    for path in sorted(get_settings().backup_dir.glob("qc-*.sqlite3")):
        match = re.fullmatch(r"qc-(\d{8})\.sqlite3", path.name, re.ASCII)
        if match is None or not path.is_file() or path.is_symlink():
            continue
        try:
            day = datetime.strptime(match[1], "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def ensure_today() -> Path | None:
    settings = get_settings()
    if not settings.db_path.is_file():
        return None
    day = today_bj()
    target = settings.backup_dir / f"qc-{day:%Y%m%d}.sqlite3"
    if not target.exists():
        backup_now(day)
        prune(settings.backup_keep_days, day)
    return target


if __name__ == "__main__":
    result = backup_now()
    removed = prune(get_settings().backup_keep_days)
    print(f"备份完成：{result}")
    print(f"清理过期备份：{len(removed)} 个")
