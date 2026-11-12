import shutil
import subprocess
from pathlib import Path

import soundfile


class TranscodeError(Exception):
    pass


def to_wav(src: Path, dst: Path, max_seconds: int, rate: int) -> float:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise TranscodeError("找不到 ffmpeg")
    stderr = ""
    try:
        result = subprocess.run(
            [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(src), "-t", str(max_seconds), "-ac", "1", "-ar", str(rate),
             "-sample_fmt", "s16", "-f", "wav", str(dst)],
            capture_output=True, timeout=60,
        )
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            raise TranscodeError(f"音频转码失败：{stderr[-300:]}")
        info = soundfile.info(str(dst))
        if info.frames == 0:
            raise TranscodeError(f"转码音频为空：{stderr[-300:]}")
        return float(info.duration)
    except TranscodeError:
        raise
    except subprocess.TimeoutExpired as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace")
        raise TranscodeError(f"音频转码超时：{detail[-300:]}") from exc
    except Exception as exc:
        raise TranscodeError(f"音频转码失败：{exc}；{stderr[-300:]}") from exc


def to_wav16k(src: Path, dst: Path, max_seconds: int) -> float:
    return to_wav(src, dst, max_seconds, 16000)
