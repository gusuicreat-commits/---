import io
import importlib
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile
from fastapi.testclient import TestClient

# tests/asr 与服务包同名，优先加载仓库根目录下的服务包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if "asr" in sys.modules and Path(sys.modules["asr"].__file__).resolve().parent == Path(__file__).resolve().parent:
    del sys.modules["asr"]
    importlib.import_module("asr")

from asr import denoise, server, transcode
from asr.config import ROOT_DIR, get_config
from asr.engine import FakeEngine, join_text, make_engine


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="找不到 ffmpeg")


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_ENGINE", "fake")
    monkeypatch.setenv("ASR_DENOISE", "1")
    monkeypatch.setenv("ASR_FRONTEND_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("ASR_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ASR_MAX_SECONDS", "60")
    monkeypatch.setenv("ASR_FAKE_TEXT", "直径一千二百五十")
    monkeypatch.setenv("ASR_PUNC_GATE", "1")
    monkeypatch.setenv("ASR_PUNC_GAP_MS", "150")


@pytest.fixture
def audio_bytes():
    samples = np.arange(44100) / 44100
    signal = 0.2 * np.sin(2 * np.pi * 440 * samples)
    audio = io.BytesIO()
    soundfile.write(audio, np.column_stack([signal, signal]), 44100, format="WAV")
    return audio.getvalue()


def upload(client, audio, **kwargs):
    return client.post("/transcribe", files={"audio": ("sample.wav", audio, "audio/wav")}, **kwargs)


def write_frontend(tmp_path, monkeypatch, source):
    directory = tmp_path / "frontend"
    directory.mkdir()
    (directory / "asr_frontend.py").write_text(source, encoding="utf-8")
    monkeypatch.setenv("ASR_FRONTEND_DIR", str(directory))
    return directory


@needs_ffmpeg
def test_missing_frontend_passthrough(audio_bytes):
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, audio_bytes)
        health = client.get("/health").json()
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "直径一千二百五十"
    assert body["duration_s"] == 1.0
    assert isinstance(body["elapsed_ms"], int)
    assert body["truncated"] is False
    assert body["denoise"] == {
        "enabled": True, "available": False, "action": "passthrough",
        "snr_db": None, "applied": False, "error": None,
        "engine": None, "elapsed_ms": None,
    }
    assert health == {
        "ok": True, "engine": "fake", "loaded": True,
        "denoise_enabled": True, "frontend_available": False,
        "load_error": None, "denoiser": None,
        "punc_gate": {"enabled": True, "min_gap_ms": 150},
    }


@needs_ffmpeg
def test_denoise_off(monkeypatch, audio_bytes):
    monkeypatch.setenv("ASR_DENOISE", "0")
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, audio_bytes)
    assert response.status_code == 200
    assert response.json()["denoise"]["action"] == "off"
    assert response.json()["denoise"]["enabled"] is False


@needs_ffmpeg
@pytest.mark.parametrize("action,applied", [("light", True), ("bypass", False)])
def test_frontend_success_and_vad_disabled(tmp_path, monkeypatch, audio_bytes, action, applied):
    directory = write_frontend(tmp_path, monkeypatch, f'''
import json
import shutil
from pathlib import Path

def process(in_wav, out_wav, vad=True):
    Path(__file__).with_name("vad.json").write_text(json.dumps(vad))
    shutil.copyfile(in_wav, out_wav)
    return {{"action": {action!r}, "snr_db": 12.3, "applied": {applied!r}}}
''')
    with TestClient(server.create_app(FakeEngine())) as client:
        assert client.get("/health").json()["frontend_available"] is True
        response = upload(client, audio_bytes)
    assert response.status_code == 200
    decision = response.json()["denoise"]
    assert decision["action"] == action
    assert decision["applied"] is applied
    assert decision["snr_db"] == 12.3
    assert decision["available"] is True
    assert json.loads((directory / "vad.json").read_text()) is False


@needs_ffmpeg
def test_frontend_exception_falls_back(tmp_path, monkeypatch, audio_bytes):
    write_frontend(tmp_path, monkeypatch, '''
def process(in_wav, out_wav, vad=False):
    raise RuntimeError("降噪故障")
''')
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, audio_bytes)
    assert response.status_code == 200
    assert response.json()["text"] == "直径一千二百五十"
    assert response.json()["denoise"]["action"] == "error"
    assert response.json()["denoise"]["error"] == "降噪故障"


@needs_ffmpeg
def test_transcoded_audio_format(audio_bytes):
    class InspectEngine(FakeEngine):
        def transcribe(self, wav, hotwords):
            info = soundfile.info(str(wav))
            assert info.samplerate == 16000
            assert info.channels == 1
            assert info.subtype == "PCM_16"
            assert info.frames == 16000
            return super().transcribe(wav, hotwords)

    original = soundfile.info(io.BytesIO(audio_bytes))
    assert original.samplerate == 44100
    assert original.channels == 2
    with TestClient(server.create_app(InspectEngine())) as client:
        assert upload(client, audio_bytes).status_code == 200


@needs_ffmpeg
@pytest.mark.parametrize("failure", [None, "bad_audio", "engine"])
def test_temporary_directory_deleted(tmp_path, monkeypatch, audio_bytes, failure):
    directories = []
    original_mkdtemp = server.tempfile.mkdtemp

    def tracked_mkdtemp(prefix):
        directory = original_mkdtemp(prefix=prefix, dir=tmp_path)
        directories.append(Path(directory))
        return directory

    class FailingEngine(FakeEngine):
        def transcribe(self, wav, hotwords):
            raise RuntimeError("识别引擎故障")

    monkeypatch.setattr(server.tempfile, "mkdtemp", tracked_mkdtemp)
    engine = FailingEngine() if failure == "engine" else FakeEngine()
    payload = b"\x00\xff\x12\x80" * 32 if failure == "bad_audio" else audio_bytes
    with TestClient(server.create_app(engine)) as client:
        response = upload(client, payload)
    expected_status = {None: 200, "bad_audio": 422, "engine": 500}[failure]
    assert response.status_code == expected_status
    if failure == "engine":
        assert response.json() == {"error": {"code": "ASR_FAILED", "message": "识别失败，请手动填写"}}
    assert len(directories) == 1
    assert all(not directory.exists() for directory in directories)


@needs_ffmpeg
def test_random_bytes_bad_audio():
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, b"\x00\xff\x12\x80" * 32)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "BAD_AUDIO", "message": "音频无法解析"}}


def test_empty_upload_bad_audio():
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, b"")
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "BAD_AUDIO", "message": "音频无法解析"}}


def test_not_ready_and_health():
    engine = FakeEngine()
    engine.loaded = False
    with TestClient(server.create_app(engine)) as client:
        response = upload(client, b"audio")
        health = client.get("/health").json()
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "ASR_NOT_READY", "message": "识别服务正在启动，请稍后再试"},
    }
    assert health["ok"] is False
    assert health["loaded"] is False


@needs_ffmpeg
def test_decision_log_without_text(tmp_path, audio_bytes):
    with TestClient(server.create_app(FakeEngine())) as client:
        assert upload(client, audio_bytes).status_code == 200
    log = (tmp_path / "logs/asr.jsonl").read_text(encoding="utf-8")
    assert len(log.splitlines()) == 1
    assert "直径一千二百五十" not in log
    entry = json.loads(log)
    assert entry["timestamp"].endswith("+08:00")
    assert entry["duration_s"] == 1.0
    assert entry["text_length"] == len("直径一千二百五十")
    assert entry["denoise"]["action"] == "passthrough"
    assert set(entry) == {"timestamp", "duration_s", "elapsed_ms", "denoise", "text_length", "punc_gate"}


def test_failed_requests_also_append_log(tmp_path):
    with TestClient(server.create_app(FakeEngine())) as client:
        assert upload(client, b"").status_code == 422
        assert upload(client, b"").status_code == 422
    entries = (tmp_path / "logs/asr.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(entries) == 2
    assert all(json.loads(line)["text_length"] == 0 for line in entries)


@pytest.mark.parametrize("results,expected", [
    ([{"text": "直 径 一 千"}, {"text": "二百五十"}], "直径一千二百五十"),
    ([{"text": "合同号 20261110 10"}], "合同号 20261110 10"),
    ([{"text": "A B 12 34，直 径"}], "A B 12 34，直径"),
    ([], ""),
])
def test_join_text(results, expected):
    assert join_text(results) == expected


@needs_ffmpeg
@pytest.mark.parametrize("words", [None, "直径 长度", ""])
def test_default_and_custom_hotwords(audio_bytes, words):
    class HotwordEngine(FakeEngine):
        def transcribe(self, wav, hotwords):
            self.received = hotwords
            return super().transcribe(wav, hotwords)

    engine = HotwordEngine()
    data = {} if words is None else {"hotwords": words}
    with TestClient(server.create_app(engine)) as client:
        assert upload(client, audio_bytes, data=data).status_code == 200
    expected = server.HOTWORDS_FILE.read_text(encoding="utf-8").split() if words is None else words.split()
    assert engine.received == expected


@needs_ffmpeg
def test_duration_truncated(monkeypatch, audio_bytes):
    monkeypatch.setenv("ASR_MAX_SECONDS", "1")
    signal, rate = soundfile.read(io.BytesIO(audio_bytes))
    longer = io.BytesIO()
    soundfile.write(longer, np.concatenate([signal, signal]), rate, format="WAV")
    with TestClient(server.create_app(FakeEngine())) as client:
        response = upload(client, longer.getvalue())
    assert response.status_code == 200
    assert response.json()["duration_s"] == 1.0
    assert response.json()["truncated"] is True


@pytest.mark.parametrize("empty", [False, True])
def test_frontend_missing_or_empty_output(tmp_path, monkeypatch, empty):
    body = '    Path(out_wav).write_bytes(b"")\n' if empty else ""
    directory = write_frontend(tmp_path, monkeypatch,
        "from pathlib import Path\n"
        "def process(in_wav, out_wav, vad=False):\n" + body +
        '    return {"action": "light", "snr_db": 12.3, "applied": True}\n')
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"audio")
    selected, decision = denoise.apply(wav, tmp_path / "output.wav", True, directory)
    assert selected == wav
    assert decision["action"] == "error"
    assert decision["error"] == "降噪输出文件不存在或为空"


def test_frontend_load_error_and_directory_cache(tmp_path, monkeypatch):
    directory = write_frontend(tmp_path, monkeypatch, 'raise RuntimeError("加载故障")\n')
    assert denoise.load_frontend(directory) is None
    (directory / "asr_frontend.py").write_text("value = 1\n", encoding="utf-8")
    assert denoise.load_frontend(directory) is None


def test_config_reloads_and_relative_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASR_LOG_DIR", "logs/custom")
    monkeypatch.setenv("ASR_FRONTEND_DIR", "asr/custom")
    monkeypatch.setenv("ASR_HOST", "localhost")
    monkeypatch.setenv("ASR_PORT", "9999")
    config = get_config()
    assert config.log_dir == ROOT_DIR / "logs/custom"
    assert config.frontend_dir == ROOT_DIR / "asr/custom"
    assert config.host == "localhost"
    assert config.port == 9999
    monkeypatch.setenv("ASR_FAKE_TEXT", "长度一百")
    assert FakeEngine().transcribe(None, []) == "长度一百"
    monkeypatch.setenv("ASR_DENOISE", "0")
    assert get_config().denoise is False


def test_default_engine_startup_uses_fake():
    with TestClient(server.create_app()) as client:
        assert client.get("/health").json()["engine"] == "fake"
    with pytest.raises(ValueError, match="不支持的识别引擎"):
        make_engine("unknown")


def test_model_load_runs_in_background(monkeypatch):
    finished = threading.Event()

    class SlowEngine(FakeEngine):
        loaded = False

        def load(self):
            time.sleep(0.3)
            self.loaded = True
            finished.set()

    engine = SlowEngine()
    monkeypatch.setattr(server, "make_engine", lambda kind: engine)
    with TestClient(server.create_app()) as client:
        health = client.get("/health").json()
        assert health["loaded"] is False
        assert health["load_error"] is None
        response = upload(client, b"audio")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ASR_NOT_READY"
        assert finished.wait(timeout=2)
        assert client.get("/health").json()["loaded"] is True


def test_model_load_failure_exposed_and_logged(monkeypatch, caplog):
    class FailedEngine(FakeEngine):
        loaded = False

        def load(self):
            raise RuntimeError("模拟模型加载故障")

    application = server.create_app(FailedEngine())
    with TestClient(application) as client:
        deadline = time.monotonic() + 2
        health = client.get("/health").json()
        while health["load_error"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
            health = client.get("/health").json()
        assert health["load_error"] == "模拟模型加载故障"
        assert health["loaded"] is False
    assert "识别模型加载失败" in caplog.text


def test_cors():
    with TestClient(server.create_app(FakeEngine())) as client:
        response = client.options("/transcribe", headers={
            "Origin": "http://127.0.0.1:8600", "Access-Control-Request-Method": "POST",
        })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8600"


def test_missing_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(transcode.shutil, "which", lambda executable: None)
    with pytest.raises(transcode.TranscodeError, match="找不到 ffmpeg"):
        transcode.to_wav16k(tmp_path / "input", tmp_path / "output", 60)


def test_transcode_error_stderr_tail(monkeypatch, tmp_path):
    detail = "故障" * 300
    monkeypatch.setattr(transcode.shutil, "which", lambda executable: "/fake/ffmpeg")
    monkeypatch.setattr(transcode.subprocess, "run", lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 1, b"", detail.encode("utf-8")))
    with pytest.raises(transcode.TranscodeError) as error:
        transcode.to_wav16k(tmp_path / "input", tmp_path / "output", 60)
    assert str(error.value) == "音频转码失败：" + detail[-300:]
