"""通过常驻子进程使用独立环境中的 DeepFilterNet3。"""

import atexit
import json
import math
import os
import queue
import subprocess
import threading
from pathlib import Path

NAME = "deepfilternet"
MODEL = "DeepFilterNet3"
INPUT_RATE = 48000
ROOT_DIR = Path(__file__).resolve().parents[2]


def _env_path(name, default):
    value = Path(os.environ.get(name, str(default))).expanduser()
    # 保留解释器符号链接，否则虚拟环境会退化为基础 Python 环境。
    return Path(os.path.abspath(value if value.is_absolute() else ROOT_DIR / value))


def _number(name, default=None):
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        number = float(value)
        if not math.isfinite(number) or (name.endswith("TIMEOUT") and number <= 0):
            raise ValueError
        return number
    except ValueError:
        raise ValueError(f"{name} 必须为有效数字，超时秒数必须大于零") from None


def _atten_limit():
    # 实测不限衰减会明显降低识别率，默认最多衰减 6 dB；设为 off 表示不限制。
    if os.environ.get("ASR_DF_ATTEN_LIM_DB", "").strip().lower() == "off":
        return None
    return _number("ASR_DF_ATTEN_LIM_DB", 6.0)


class _Worker:
    def __init__(self):
        self.lock = threading.RLock()
        self.io_lock = threading.Lock()
        self.proc = None
        self.lines = None
        self.log = None
        self.ready = False
        self.starting = False
        self.load_ms = None
        self.starts = 0
        self.error = None
        self.sequence = 0
        self.attempted = False
        self.closed = False
        self.reader = None

    def status(self):
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            return {"name": NAME, "model": MODEL, "ready": self.ready and alive,
                    "starting": self.starting, "load_ms": self.load_ms,
                    "starts": self.starts,
                    "error": self.error or ("降噪进程异常退出" if self.ready and not alive else None)}

    @staticmethod
    def _read_lines(proc, lines):
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    @staticmethod
    def _receive(lines, timeout):
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("降噪超时") from None
        if line is None:
            raise RuntimeError("降噪进程异常退出")
        try:
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, TypeError) as exc:
            raise RuntimeError("降噪进程回复格式错误") from exc

    def _stop(self, force=False):
        with self.lock:
            proc, log, reader = self.proc, self.log, self.reader
            self.proc = self.log = self.lines = None
            self.reader = None
            self.ready = False
        if proc is not None:
            try:
                if force and proc.poll() is None:
                    proc.terminate()
                if proc.stdin:
                    proc.stdin.close()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except (OSError, BrokenPipeError):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            finally:
                if reader is not None:
                    reader.join(timeout=1)
                if proc.stdout:
                    proc.stdout.close()
        if log is not None:
            log.close()

    def warmup(self):
        with self.io_lock:
            with self.lock:
                if self.closed:
                    self.starting = False
                    return self.status()
                if self.status()["ready"]:
                    return self.status()
                self.starting = True
                self.attempted = True
                self.error = None
                self.load_ms = None
            self._stop(force=True)
            try:
                python = _env_path("ASR_DF_PYTHON", ROOT_DIR / ".venv-df/bin/python")
                if not python.is_file():
                    raise RuntimeError("未找到降噪环境 .venv-df，请运行 asr/deepfilter/setup_env.sh")
                worker = _env_path("ASR_DF_WORKER", Path(__file__).with_name("worker.py"))
                log_dir = _env_path("ASR_LOG_DIR", ROOT_DIR / "logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                # 启动和关闭以状态锁串行化，避免退出期间遗留新进程。
                with self.lock:
                    if self.closed:
                        return self.status()
                    self.log = (log_dir / "deepfilter.log").open("a", encoding="utf-8")
                    self.proc = subprocess.Popen(
                        [str(python), "-u", str(worker)], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=self.log, text=True,
                        encoding="utf-8", bufsize=1, cwd=ROOT_DIR,
                    )
                    self.starts += 1
                    proc = self.proc
                    lines = self.lines = queue.Queue()
                    self.reader = threading.Thread(target=self._read_lines, args=(proc, lines),
                                                   daemon=True, name="deepfilter-reader")
                    self.reader.start()
                result = self._receive(lines, _number("ASR_DF_START_TIMEOUT", 120))
                if not result.get("ready"):
                    raise RuntimeError(result.get("error") or "降噪模型加载失败")
                with self.lock:
                    if not self.closed and self.proc is proc:
                        self.ready = True
                        self.load_ms = int(result["load_ms"])
            except Exception as exc:
                with self.lock:
                    self.error = f"降噪启动失败：{exc}"[:200]
                    if str(exc).startswith("未找到降噪环境"):
                        self.error = str(exc)
                self._stop(force=True)
            finally:
                with self.lock:
                    self.starting = False
            return self.status()

    def _background(self):
        with self.lock:
            if self.starting or self.closed:
                return
            self.starting = True
            self.attempted = True
            threading.Thread(target=self.warmup, daemon=True, name="deepfilter-loader").start()

    def process(self, in_wav, out_wav, vad=False):
        with self.lock:
            if not self.status()["ready"]:
                # 已启动却崩溃时也只安排一次恢复，不让请求承担加载等待。
                crashed = self.ready and not self.starting
                if not self.attempted or crashed:
                    self._background()
                raise RuntimeError("降噪进程异常退出" if crashed else "降噪模型尚未就绪")
        with self.io_lock:
            with self.lock:
                if not self.status()["ready"]:
                    raise RuntimeError("降噪模型尚未就绪")
                proc, lines = self.proc, self.lines
            source, target = Path(in_wav), Path(out_wav)
            if (source.resolve() == target.resolve()
                    or (target.exists() and source.samefile(target))):
                raise RuntimeError("降噪输出不能覆盖原始录音")
            self.sequence += 1
            request = {"id": self.sequence, "in": str(Path(in_wav).resolve()),
                       "out": str(Path(out_wav).resolve()),
                       "atten_lim_db": _atten_limit(),
                       "bypass_snr_db": _number("ASR_DF_BYPASS_SNR_DB")}
            try:
                proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                result = self._receive(lines, _number("ASR_DF_TIMEOUT", 30))
                if result.get("id") != self.sequence:
                    raise RuntimeError("降噪进程回复编号错误")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                message = "降噪超时" if isinstance(exc, TimeoutError) else "降噪进程异常退出"
                with self.lock:
                    self.error = message
                self._stop(force=True)
                self._background()
                raise RuntimeError(message) from exc
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "降噪处理失败")
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError("降噪输出文件不存在或为空")
            snr = float(result["snr_db"])
            if result["action"] not in {"bypass", "light", "strong"} or not math.isfinite(snr):
                raise RuntimeError("降噪决策格式无效")
            return {"action": result["action"], "snr_db": snr,
                    "applied": bool(result["applied"]), "elapsed_ms": int(result["elapsed_ms"])}

    def shutdown(self):
        with self.lock:
            self.closed = True
            self.starting = False
        self._stop()


_worker = _Worker()


def warmup():
    # 支持同一模块在服务生命周期结束后再次显式启动。
    with _worker.lock:
        _worker.closed = False
    return _worker.warmup()


def status():
    return _worker.status()


def process(in_wav, out_wav, vad=False):
    return _worker.process(in_wav, out_wav, vad=vad)


def shutdown():
    _worker.shutdown()


atexit.register(shutdown)
