import importlib.util
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path


logger = logging.getLogger(__name__)
_load_lock = threading.Lock()


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
    # lru_cache 允许并发执行首次加载，额外串行化以确保只有一个模块单例。
    with _load_lock:
        return _load_frontend(frontend_dir.resolve())


def apply(wav: Path, out: Path, enabled: bool, frontend_dir: Path,
          src48: Path | None = None) -> tuple[Path, dict]:
    decision = {
        "enabled": enabled, "available": False,
        "action": "passthrough" if enabled else "off",
        "snr_db": None, "applied": False, "error": None,
        "engine": None, "elapsed_ms": None,
    }
    if not enabled:
        return wav, decision
    frontend = load_frontend(frontend_dir)
    if frontend is None:
        return wav, decision
    decision["available"] = True
    decision["engine"] = getattr(frontend, "NAME", "frontend")
    started = time.perf_counter()
    try:
        source = (src48 if getattr(frontend, "INPUT_RATE", 16000) == 48000
                  and src48 is not None and src48.is_file() else wav)
        result = frontend.process(str(source), str(out), vad=False)
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
    finally:
        decision["elapsed_ms"] = int((time.perf_counter() - started) * 1000)


def _call(frontend_dir: Path, name: str):
    module = load_frontend(frontend_dir)
    method = getattr(module, name, None)
    if method is None:
        return None
    try:
        return method()
    except Exception:
        logger.exception("降噪模块调用失败：%s", name)
        return None


def warmup(frontend_dir: Path) -> dict | None:
    return _call(frontend_dir, "warmup")


def status(frontend_dir: Path) -> dict | None:
    return _call(frontend_dir, "status")


def input_rate(frontend_dir: Path) -> int:
    module = load_frontend(frontend_dir)
    try:
        return int(getattr(module, "INPUT_RATE", 16000))
    except (TypeError, ValueError):
        logger.exception("降噪模块输入采样率无效")
        return 16000


def shutdown(frontend_dir: Path):
    _call(frontend_dir, "shutdown")
