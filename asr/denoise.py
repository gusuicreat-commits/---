import importlib.util
import logging
from functools import lru_cache
from pathlib import Path


logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _load_frontend(frontend_dir: Path):
    source = frontend_dir / "asr_frontend.py"
    if not source.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            f"asr_frontend_{abs(hash(frontend_dir))}", source,
        )
        if spec is None or spec.loader is None:
            raise ImportError("无法创建降噪模块加载器")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        logger.exception("降噪模块加载失败，使用原始音频")
        return None


def load_frontend(frontend_dir: Path):
    return _load_frontend(frontend_dir.resolve())


def apply(wav: Path, out: Path, enabled: bool, frontend_dir: Path) -> tuple[Path, dict]:
    decision = {
        "enabled": enabled, "available": False,
        "action": "passthrough" if enabled else "off",
        "snr_db": None, "applied": False, "error": None,
    }
    if not enabled:
        return wav, decision
    frontend = load_frontend(frontend_dir)
    if frontend is None:
        return wav, decision
    decision["available"] = True
    try:
        result = frontend.process(str(wav), str(out), vad=False)
        if not out.is_file() or out.stat().st_size == 0:
            raise ValueError("降噪输出文件不存在或为空")
        action = result["action"]
        if action not in {"bypass", "light", "strong"}:
            raise ValueError("降噪决策档位无效")
        decision.update(
            action=action, snr_db=float(result["snr_db"]), applied=bool(result["applied"]),
        )
        return out, decision
    except Exception as exc:
        logger.exception("降噪处理失败，使用原始音频")
        decision.update(action="error", snr_db=None, applied=False, error=str(exc)[:200])
        return wav, decision
