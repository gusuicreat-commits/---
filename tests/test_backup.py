import sqlite3
import subprocess
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import backup
from app.config import ROOT, get_settings


@pytest.fixture(autouse=True)
def backup_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("QC_BACKUP_DIR", str(tmp_path / "备份"))


def test_backup_database_rows(client, contract):
    day = datetime(2026, 11, 11, 1, tzinfo=ZoneInfo("UTC"))
    path = backup.backup_now(day)
    assert path == get_settings().backup_dir / "qc-20261111.sqlite3"
    with closing(sqlite3.connect(get_settings().db_path)) as source:
        with closing(sqlite3.connect(path)) as destination:
            assert destination.execute("SELECT count(*) FROM contract").fetchone() == source.execute(
                "SELECT count(*) FROM contract").fetchone()
            assert destination.execute("SELECT ht_code FROM contract").fetchone() == (contract,)
            assert destination.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_backup_overwrites_atomically(client, contract):
    day = backup.today_bj()
    path = backup.backup_now(day)
    client.post("/api/contracts", json={"ht_code": "20261110-20", "name": "第二位客户"})
    assert backup.backup_now(day) == path
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM contract").fetchone() == (2,)
    assert list(path.parent.glob("*")) == [path]


def test_prune_only_matching_old_dates(tmp_path):
    today = date(2026, 11, 10)
    directory = get_settings().backup_dir
    directory.mkdir()
    old_name = f"qc-{today - timedelta(days=31):%Y%m%d}.sqlite3"
    names = [old_name, f"qc-{today - timedelta(days=30):%Y%m%d}.sqlite3",
             f"qc-{today:%Y%m%d}.sqlite3", "qc-19990101.sqlite3.bak", "其他.sqlite3",
             "qc-20260230.sqlite3", "qc-2026111.sqlite3", "qc-abcdefgh.sqlite3"]
    for name in names:
        (directory / name).write_bytes(b"")
    (directory / "qc-19990101.sqlite3").mkdir()
    external = tmp_path / "外部备份.sqlite3"
    external.write_bytes(b"")
    (directory / "qc-19990102.sqlite3").symlink_to(external)
    assert backup.prune(30, today) == [directory / old_name]
    assert all((directory / name).exists() for name in names if name != old_name)
    assert external.exists()


def test_ensure_today_no_repeat(client, monkeypatch):
    fixed = date(2026, 12, 1)
    monkeypatch.setattr(backup, "today_bj", lambda: fixed)
    path = backup.ensure_today()
    assert path.name == "qc-20261201.sqlite3"
    before = path.stat().st_mtime_ns

    def unexpected_backup(*args, **kwargs):
        pytest.fail("当天备份已经存在，不应重复备份")

    monkeypatch.setattr(backup, "backup_now", unexpected_backup)
    assert backup.ensure_today() == path
    assert path.stat().st_mtime_ns == before


def test_ensure_missing_database_returns_none():
    assert not get_settings().db_path.exists()
    assert backup.ensure_today() is None
    assert not get_settings().backup_dir.exists()


def test_missing_source_does_not_create_empty_database():
    with pytest.raises(sqlite3.OperationalError):
        backup.backup_now()
    assert not get_settings().db_path.exists()
    assert list(get_settings().backup_dir.iterdir()) == []


def test_startup_backup_failure_only_logs(monkeypatch, caplog):
    from app.main import create_app

    def fail():
        raise OSError("模拟备份磁盘故障")

    monkeypatch.setattr(backup, "ensure_today", fail)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json() == {"ok": True}
    assert "每日数据库备份失败" in caplog.text


def test_cli_backup(client):
    result = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "app.backup"],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "备份完成：" in result.stdout
    assert "清理过期备份：" in result.stdout
    assert len(list(get_settings().backup_dir.glob("qc-*.sqlite3"))) == 1


def test_config_relative_paths_and_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QC_BACKUP_DIR", "custom/backup")
    monkeypatch.setenv("QC_BACKUP_KEEP_DAYS", "15")
    monkeypatch.setenv("QC_ASR_TIMEOUT", "42")
    settings = get_settings()
    assert settings.backup_dir == ROOT / "custom/backup"
    assert settings.backup_keep_days == 15
    assert settings.asr_timeout == 42
    for variable in ["QC_STORAGE", "QC_BACKUP_DIR", "QC_BACKUP_KEEP_DAYS", "QC_ASR_TIMEOUT"]:
        monkeypatch.delenv(variable, raising=False)
    settings = get_settings()
    assert settings.storage == "local"
    assert settings.backup_dir == ROOT / "data/backup"
    assert settings.backup_keep_days == 30
    assert settings.asr_timeout == 90
