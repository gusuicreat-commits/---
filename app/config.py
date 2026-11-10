import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _path(variable: str, default: Path) -> Path:
    path = Path(os.environ.get(variable, str(default))).expanduser()
    return path if path.is_absolute() else ROOT / path


class Settings:
    def __init__(self):
        self.db_path: Path = _path("QC_DB_PATH", ROOT / "data" / "qc.sqlite3")
        self.export_dir: Path = _path("QC_EXPORT_DIR", ROOT / "exports")
        self.audio_dir: Path = _path("QC_AUDIO_DIR", ROOT / "data" / "audio")
        self.demo_audio_dir: Path = _path("QC_DEMO_AUDIO_DIR", ROOT / "data" / "demo-audio")
        self.asr_url: str = os.environ.get("QC_ASR_URL", "http://127.0.0.1:8601")
        self.web_dir: Path = _path("QC_WEB_DIR", ROOT / "web")
        self.tz: str = os.environ.get("QC_TZ", "Asia/Shanghai")
        self.storage: str = os.environ.get("QC_STORAGE", "local")
        self.backup_dir: Path = _path("QC_BACKUP_DIR", ROOT / "data" / "backup")
        self.backup_keep_days: int = int(os.environ.get("QC_BACKUP_KEEP_DAYS", "30"))
        self.asr_timeout: float = float(os.environ.get("QC_ASR_TIMEOUT", "90"))


def get_settings() -> Settings:
    return Settings()
