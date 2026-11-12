"""只用协议替身验证降噪集成，不导入真实模型。"""

import hashlib
import importlib
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import soundfile
from fastapi.testclient import TestClient

# 避免测试目录与服务包同名导致导入测试包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if "asr" in sys.modules and Path(sys.modules["asr"].__file__).resolve().parent == Path(__file__).resolve().parent:
    del sys.modules["asr"]
    importlib.import_module("asr")

from asr import denoise, server
from asr.config import ROOT_DIR, get_config
from asr.engine import FakeEngine

FRONTEND = ROOT_DIR / "asr/deepfilter"
FAKE_WORKER = Path(__file__).with_name("fake_df_worker.py")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="找不到 ffmpeg")


@pytest.fixture
def frontend(monkeypatch, tmp_path):
    denoise.shutdown(FRONTEND)
    denoise._load_frontend.cache_clear()
    for name in ("ASR_DF_ATTEN_LIM_DB", "ASR_DF_BYPASS_SNR_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ASR_DF_PYTHON", sys.executable)
    monkeypatch.setenv("ASR_DF_WORKER", str(FAKE_WORKER))
    monkeypatch.setenv("ASR_DF_TIMEOUT", "0.5")
    monkeypatch.setenv("ASR_DF_START_TIMEOUT", "10")
    monkeypatch.setenv("ASR_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ASR_FRONTEND_DIR", str(FRONTEND))
    monkeypatch.setenv("ASR_DENOISE", "1")
    monkeypatch.setenv("ASR_ENGINE", "fake")
    monkeypatch.setenv("FAKE_DF_MODE", "success")
    monkeypatch.setenv("FAKE_DF_MARKER", str(tmp_path / "failure"))
    monkeypatch.setenv("FAKE_DF_CAPTURE", str(tmp_path / "received.jsonl"))
    module = denoise.load_frontend(FRONTEND)
    processes = []
    original_popen = module.subprocess.Popen

    def track(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        processes.append(proc)
        return proc

    # subprocess 是共享标准库模块，因此也跟踪 ffmpeg，但不改变其行为。
    monkeypatch.setattr(module.subprocess, "Popen", track)
    yield module
    module.shutdown()
    assert all(proc.poll() is not None for proc in processes)
    denoise._load_frontend.cache_clear()


@pytest.fixture
def inputs(tmp_path):
    paths = []
    for name, rate, channels in [("upload.wav", 44100, 2), ("in16.wav", 16000, 1), ("in48.wav", 48000, 1)]:
        t = np.arange(rate) / rate
        signal = 0.1 * np.sin(2 * np.pi * 230 * t)
        if channels == 2:
            signal = np.column_stack([signal, signal])
        path = tmp_path / name
        soundfile.write(path, signal, rate, subtype="PCM_16")
        paths.append(path)
    return paths


def wait_ready(module, starts=1):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = module.status()
        if state["ready"] and state["starts"] == starts:
            return state
        time.sleep(0.01)
    pytest.fail(f"降噪进程未恢复：{module.status()}")


def test_singleton_and_success(frontend, inputs, tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(state["ready"] for state in pool.map(lambda _: frontend.warmup(), range(4)))
    assert denoise.warmup(FRONTEND)["ready"] is True
    for index in range(3):
        selected, decision = denoise.apply(inputs[1], tmp_path / f"out{index}.wav", True, FRONTEND)
        info = soundfile.info(selected)
        assert info.samplerate == 16000 and info.channels == 1
        assert info.subtype == "PCM_16" and info.frames > 0
        assert decision["action"] == "light" and decision["applied"] is True
        assert isinstance(decision["snr_db"], float)
        assert decision["engine"] == "deepfilternet"
        assert isinstance(decision["elapsed_ms"], int)
    assert frontend.status() == {"name": "deepfilternet", "model": "DeepFilterNet3",
                                 "ready": True, "starting": False, "starts": 1,
                                 "load_ms": 8, "error": None}


def test_input_rate_and_immutable_sources(frontend, inputs, tmp_path, monkeypatch):
    frontend.warmup()
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs]
    monkeypatch.setenv("ASR_DF_ATTEN_LIM_DB", "12")
    monkeypatch.setenv("ASR_DF_BYPASS_SNR_DB", "25")
    for index, source in enumerate([inputs[2], None, tmp_path / "missing"]):
        denoise.apply(inputs[1], tmp_path / f"out{index}.wav", True, FRONTEND, src48=source)
    received = [json.loads(line) for line in (tmp_path / "received.jsonl").read_text().splitlines()]
    assert [item["in_sr"] for item in received] == [48000, 16000, 16000]
    assert all(item["atten_lim_db"] == 12 and item["bypass_snr_db"] == 25 for item in received)
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs]


@pytest.mark.parametrize("mode,message,restarts", [
    ("ok_error", "模拟降噪处理失败", False),
    ("timeout_once", "降噪超时", True),
    ("crash_once", "降噪进程异常退出", True),
])
def test_failures_and_restart(frontend, inputs, tmp_path, monkeypatch, mode, message, restarts):
    monkeypatch.setenv("FAKE_DF_MODE", mode)
    frontend.warmup()
    selected, decision = denoise.apply(inputs[1], tmp_path / "failed.wav", True, FRONTEND, inputs[2])
    assert selected == inputs[1]
    assert decision["action"] == "error"
    assert decision["error"] == message
    if restarts:
        wait_ready(frontend, 2)
        selected, decision = denoise.apply(inputs[1], tmp_path / "recovered.wav", True, FRONTEND, inputs[2])
        assert selected != inputs[1] and decision["action"] == "light"
    else:
        assert frontend.status()["starts"] == 1


def test_crash_after_reply_recovers(frontend, inputs, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DF_MODE", "after_reply_once")
    frontend.warmup()
    assert frontend.process(inputs[1], tmp_path / "first.wav")["action"] == "light"
    frontend._worker.proc.wait(timeout=5)
    selected, decision = denoise.apply(inputs[1], tmp_path / "second.wav", True, FRONTEND)
    assert selected == inputs[1] and decision["action"] == "error"
    wait_ready(frontend, 2)
    assert frontend.process(inputs[1], tmp_path / "third.wav")["action"] == "light"


def test_missing_python(frontend, inputs, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_DF_PYTHON", "missing-python")
    expected = "未找到降噪环境 .venv-df，请运行 asr/deepfilter/setup_env.sh"
    assert frontend.warmup()["error"] == expected
    assert frontend.status()["starts"] == 0
    selected, decision = denoise.apply(inputs[1], tmp_path / "out.wav", True, FRONTEND)
    assert selected == inputs[1] and decision["action"] == "error"


def test_not_ready_does_not_block(frontend, inputs, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DF_MODE", "slow")
    started = time.monotonic()
    selected, decision = denoise.apply(inputs[1], tmp_path / "out.wav", True, FRONTEND)
    assert time.monotonic() - started < 1
    assert selected == inputs[1] and decision["error"] == "降噪模型尚未就绪"
    assert frontend.status()["starting"] is True
    wait_ready(frontend)
    assert frontend.status()["starts"] == 1


@pytest.mark.parametrize("mode", ["ready_error", "slow"])
def test_ready_failure(frontend, monkeypatch, mode):
    monkeypatch.setenv("FAKE_DF_MODE", mode)
    if mode == "slow":
        monkeypatch.setenv("ASR_DF_START_TIMEOUT", "0.05")
    state = frontend.warmup()
    assert not state["ready"] and not state["starting"]
    assert "降噪" in state["error"]
    assert frontend._worker.proc is None


@needs_ffmpeg
def test_off_does_not_start(frontend, inputs, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_DENOISE", "0")
    selected, decision = denoise.apply(inputs[1], tmp_path / "out.wav", get_config().denoise, FRONTEND)
    assert selected == inputs[1] and decision["action"] == "off"
    assert decision["elapsed_ms"] is None and frontend.status()["starts"] == 0
    with TestClient(server.create_app(FakeEngine())) as client:
        response = client.post("/transcribe", files={"audio": ("sample.wav", inputs[0].read_bytes())})
        assert response.status_code == 200 and response.json()["denoise"]["action"] == "off"
        assert client.get("/health").json()["denoiser"]["starts"] == 0


def test_parallel_requests_are_serialized(frontend, inputs, tmp_path):
    frontend.warmup()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: frontend.process(inputs[1], tmp_path / f"out{i}.wav"), range(8)))
    assert all(result["action"] == "light" for result in results)
    received = [json.loads(line)["id"] for line in (tmp_path / "received.jsonl").read_text().splitlines()]
    assert received == list(range(1, 9))
    assert frontend.status()["starts"] == 1


def test_shutdown_during_startup(frontend, monkeypatch):
    monkeypatch.setenv("FAKE_DF_MODE", "slow")
    loader = threading.Thread(target=frontend.warmup)
    loader.start()
    deadline = time.monotonic() + 3
    while frontend.status()["starts"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    frontend.shutdown()
    loader.join(timeout=5)
    assert not loader.is_alive()
    assert not frontend.status()["ready"] and not frontend.status()["starting"]


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["success", "ok_error"])
def test_service_denoise_and_fallback(frontend, inputs, monkeypatch, tmp_path, mode):
    monkeypatch.setenv("FAKE_DF_MODE", mode)
    frontend.warmup()
    with TestClient(server.create_app(FakeEngine())) as client:
        assert client.get("/health").json()["denoiser"]["ready"] is True
        response = client.post("/transcribe", files={"audio": ("sample.wav", inputs[0].read_bytes(), "audio/wav")})
        assert response.status_code == 200
        body = response.json()
        assert body["text"] == get_config().fake_text
        assert body["denoise"]["engine"] == "deepfilternet"
        assert body["denoise"]["action"] == ("light" if mode == "success" else "error")
        entry = json.loads((tmp_path / "logs/asr.jsonl").read_text())
        assert entry["denoise"] == body["denoise"]
        if mode == "success":
            received = json.loads((tmp_path / "received.jsonl").read_text())
            assert received["in_sr"] == 48000
            assert Path(received["in"]).name == "converted48k.wav"
            assert not Path(received["in"]).exists()
    assert not frontend.status()["ready"]


@needs_ffmpeg
def test_48k_transcode_failure_is_nonfatal(frontend, inputs, monkeypatch, tmp_path):
    frontend.warmup()
    def fail(*args):
        raise server.TranscodeError("模拟 48k 转码失败")
    monkeypatch.setattr(server, "to_wav", fail)
    with TestClient(server.create_app(FakeEngine())) as client:
        response = client.post("/transcribe", files={"audio": ("sample.wav", inputs[0].read_bytes())})
        assert response.status_code == 200 and response.json()["denoise"]["action"] == "light"
    assert json.loads((tmp_path / "received.jsonl").read_text())["in_sr"] == 16000


def test_legacy_helpers_and_default_config(frontend, tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    assert denoise.warmup(missing) is None
    assert denoise.status(missing) is None
    assert denoise.input_rate(missing) == 16000
    denoise.shutdown(missing)
    monkeypatch.delenv("ASR_FRONTEND_DIR")
    assert get_config().frontend_dir == FRONTEND
    assert denoise.input_rate(FRONTEND) == 48000


def test_defaults_denoise_off_and_atten_limit(monkeypatch):
    # 实测结论：降噪默认关闭；打开时默认最多衰减 6 dB，off 表示不限制。
    from asr.config import get_config
    monkeypatch.delenv("ASR_DENOISE", raising=False)
    assert get_config().denoise is False
    module = denoise.load_frontend(Path(__file__).resolve().parents[2] / "asr/deepfilter")
    monkeypatch.delenv("ASR_DF_ATTEN_LIM_DB", raising=False)
    assert module._atten_limit() == 6.0
    monkeypatch.setenv("ASR_DF_ATTEN_LIM_DB", "off")
    assert module._atten_limit() is None
