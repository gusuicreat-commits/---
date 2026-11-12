"""显式开启才执行真实独立环境的降噪冒烟测试。"""

import importlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if "asr" in sys.modules and Path(sys.modules["asr"].__file__).resolve().parent == Path(__file__).resolve().parent:
    del sys.modules["asr"]
    importlib.import_module("asr")

from asr import denoise
from asr.config import ROOT_DIR


@pytest.mark.skipif(os.environ.get("ASR_DF_REAL") != "1" or not (ROOT_DIR / ".venv-df/bin/python").is_file(),
                    reason="未开启真实降噪环境测试")
def test_real_worker(tmp_path, monkeypatch):
    frontend = ROOT_DIR / "asr/deepfilter"
    denoise.shutdown(frontend)
    denoise._load_frontend.cache_clear()
    monkeypatch.setenv("ASR_DF_PYTHON", str(ROOT_DIR / ".venv-df/bin/python"))
    monkeypatch.setenv("ASR_DF_WORKER", str(frontend / "worker.py"))
    monkeypatch.setenv("ASR_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("ASR_DF_BYPASS_SNR_DB", raising=False)
    monkeypatch.delenv("ASR_DF_ATTEN_LIM_DB", raising=False)
    # 合成人声基频、谐波与起伏包络，叠加固定种子的工频噪声。
    t = np.arange(48000) / 48000
    envelope = np.sin(np.pi * t) ** 2
    voice = sum(np.sin(2 * np.pi * 180 * harmonic * t) / harmonic for harmonic in range(1, 8))
    noisy = 0.12 * envelope * voice + 0.04 * np.sin(2 * np.pi * 50 * t)
    noisy += np.random.default_rng(10).normal(0, 0.015, len(t))
    source, out = tmp_path / "voice.wav", tmp_path / "out.wav"
    soundfile.write(source, noisy, 48000)
    try:
        state = denoise.warmup(frontend)
        assert state["ready"] is True, state
        assert denoise.warmup(frontend)["ready"] is True
        module = denoise.load_frontend(frontend)
        module.process(source, out)
        assert soundfile.info(out).samplerate == 16000
        assert soundfile.info(out).frames > 0
        assert denoise.status(frontend)["starts"] == 1
    finally:
        denoise.shutdown(frontend)
        denoise._load_frontend.cache_clear()
