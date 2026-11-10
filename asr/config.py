import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    engine: str
    denoise: bool
    frontend_dir: Path
    max_seconds: int
    log_dir: Path
    fake_text: str


def _env_path(name: str, default: Path) -> Path:
    value = Path(os.environ.get(name, str(default))).expanduser()
    return (value if value.is_absolute() else ROOT_DIR / value).resolve()


def get_config() -> Config:
    return Config(
        host=os.environ.get("ASR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ASR_PORT", "8601")),
        engine=os.environ.get("ASR_ENGINE", "funasr"),
        denoise=os.environ.get("ASR_DENOISE", "1") == "1",
        frontend_dir=_env_path("ASR_FRONTEND_DIR", ROOT_DIR / "asr/vendor/clearmic"),
        max_seconds=int(os.environ.get("ASR_MAX_SECONDS", "60")),
        log_dir=_env_path("ASR_LOG_DIR", ROOT_DIR / "logs"),
        fake_text=os.environ.get("ASR_FAKE_TEXT", "直径一千二百五十"),
    )
