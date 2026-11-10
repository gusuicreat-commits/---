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
        self.asr_url: str = os.environ.get("QC_ASR_URL", "http://127.0.0.1:8601")
        self.web_dir: Path = _path("QC_WEB_DIR", ROOT / "web")
        self.tz: str = os.environ.get("QC_TZ", "Asia/Shanghai")


def get_settings() -> Settings:
    return Settings()
