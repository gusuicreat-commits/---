import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import soundfile
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from asr import denoise
from asr.config import get_config
from asr.engine import make_engine
from asr.transcode import TranscodeError, to_wav, to_wav16k


logger = logging.getLogger(__name__)
HOTWORDS_FILE = Path(__file__).resolve().with_name("hotwords.txt")


def _source_duration(src: Path) -> float | None:
    try:
        return float(soundfile.info(str(src)).duration)
    except Exception:
        executable = shutil.which("ffprobe")
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
                capture_output=True, timeout=10,
            )
            return float(result.stdout) if result.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _request_hotwords(request: Request, hotwords: str | None = Form(None)) -> str | None:
    # 显式空字符串表示不使用热词，只有未提交字段时才读取默认热词文件。
    form = await request.form()
    return "" if "hotwords" in form and hotwords is None else hotwords


def create_app(engine=None) -> FastAPI:
    injected = engine is not None
    recognizer = engine if injected else make_engine(get_config().engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        def load_model():
            try:
                recognizer.load()
            except Exception as exc:
                application.state.load_error = str(exc)
                logger.exception("识别模型加载失败")
        threading.Thread(target=load_model, daemon=True, name="asr-model-loader").start()
        config = get_config()
        if config.denoise:
            threading.Thread(target=denoise.warmup, args=(config.frontend_dir,),
                             daemon=True, name="asr-denoiser-loader").start()
        try:
            yield
        finally:
            denoise.shutdown(config.frontend_dir)

    application = FastAPI(lifespan=lifespan)
    application.state.engine = recognizer
    application.state.load_error = None
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8600", "http://localhost:8600"],
        allow_methods=["GET", "POST"], allow_headers=["*"],
    )

    @application.get("/health")
    def health():
        config = get_config()
        loaded = bool(recognizer.loaded)
        return {
            "ok": loaded, "engine": recognizer.name, "loaded": loaded,
            "denoise_enabled": config.denoise,
            "frontend_available": denoise.load_frontend(config.frontend_dir) is not None,
            "load_error": application.state.load_error,
            "denoiser": denoise.status(config.frontend_dir),
            "punc_gate": {"enabled": config.punc_gate, "min_gap_ms": config.punc_gap_ms},
        }

    @application.post("/transcribe")
    def transcribe(audio: UploadFile = File(...), hotwords: str | None = Depends(_request_hotwords)):
        started = time.perf_counter()
        config = get_config()
        directory = None
        duration = 0.0
        text = ""
        punc_gate = None
        decision = {
            "enabled": config.denoise, "available": False,
            "action": "passthrough" if config.denoise else "off",
            "snr_db": None, "applied": False, "error": None,
            "engine": None, "elapsed_ms": None,
        }
        try:
            if not recognizer.loaded:
                return _error(503, "ASR_NOT_READY", "识别服务正在启动，请稍后再试")
            directory = Path(tempfile.mkdtemp(prefix="asr-"))
            source = directory / "upload"
            with source.open("wb") as output:
                shutil.copyfileobj(audio.file, output)
            if source.stat().st_size == 0:
                return _error(422, "BAD_AUDIO", "音频无法解析")
            original_duration = _source_duration(source)
            duration = to_wav16k(source, directory / "converted.wav", config.max_seconds)
            src48 = None
            if config.denoise and denoise.input_rate(config.frontend_dir) == 48000:
                try:
                    to_wav(source, directory / "converted48k.wav", config.max_seconds, 48000)
                    src48 = directory / "converted48k.wav"
                except Exception:
                    logger.exception("48kHz 音频转码失败，使用 16kHz 音频降噪")
            wav, decision = denoise.apply(
                directory / "converted.wav", directory / "denoised.wav",
                config.denoise, config.frontend_dir, src48=src48,
            )
            words = (HOTWORDS_FILE.read_text(encoding="utf-8") if hotwords is None else hotwords).split()
            if hasattr(recognizer, "transcribe_detail"):
                detail = recognizer.transcribe_detail(wav, words)
                text = detail["text"]
                text_model = detail["text_model"]
                punc_gate = detail["punc_gate"]
            else:
                text = recognizer.transcribe(wav, words)
                text_model = text
            return {
                "text": text, "duration_s": round(duration, 2),
                "text_model": text_model, "punc_gate": punc_gate,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "denoise": decision,
                "truncated": original_duration is not None and original_duration > config.max_seconds,
            }
        except TranscodeError:
            return _error(422, "BAD_AUDIO", "音频无法解析")
        except Exception:
            logger.exception("音频识别失败")
            return _error(500, "ASR_FAILED", "识别失败，请手动填写")
        finally:
            try:
                if directory is not None:
                    shutil.rmtree(directory)
            finally:
                try:
                    config.log_dir.mkdir(parents=True, exist_ok=True)
                    entry = {
                        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                        "duration_s": round(duration, 2),
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "denoise": decision, "text_length": len(text),
                        "punc_gate": punc_gate,
                    }
                    with (config.log_dir / "asr.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    logger.exception("识别决策日志写入失败")

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(app, host=config.host, port=config.port)
