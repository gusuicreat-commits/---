"""使用构造时间戳验证停顿校正及服务接口。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asr import server
from asr.config import get_config
from asr.engine import FakeEngine, FunAsrEngine
from asr.punctuation import PUNCT, TOKEN, gate_commas


def timestamps_for(text, gaps=None):
    """构造每个发音 token 长 100ms、默认相邻无停顿的时间戳。"""
    spoken = [token for token in TOKEN.findall(text) if token not in PUNCT]
    timestamps = []
    start = 0
    for index, token in enumerate(spoken):
        if index:
            start += (gaps or {}).get((spoken[index - 1], token), 0)
        timestamps.append([start, start + 100])
        start += 100
    return timestamps


@pytest.mark.parametrize("text,gaps,expected,commas,removed", [
    (
        "绝缘电阻五百兆欧，控制线，力矩一点二牛米。",
        {("欧", "控"): 360, ("线", "力"): 40},
        "绝缘电阻五百兆欧，控制线力矩一点二牛米。", 2, 1,
    ),
    (
        "底漆，厚度九十八，面漆厚度九十九。", {("八", "面"): 260},
        "底漆厚度九十八，面漆厚度九十九。", 2, 1,
    ),
    ("面漆厚度九十五，一百。", {}, "面漆厚度九十五，一百。", 1, 0),
    (
        "直径一千二百五十，长度三千，厚度，二十光段三百。",
        {("十", "长"): 300, ("千", "厚"): 300},
        "直径一千二百五十，长度三千，厚度二十光段三百。", 3, 1,
    ),
    (
        "减速机铭牌ZQ五百，电机铭牌YZR一百六十。", {("百", "电"): 200},
        "减速机铭牌ZQ五百，电机铭牌YZR一百六十。", 1, 0,
    ),
    ("厚度。二十？光段！三百;长度；四十.直径?一百!", {},
     "厚度。二十？光段！三百;长度；四十.直径?一百!", 0, 0),
    ("底漆，厚度。", {("漆", "厚"): 150}, "底漆，厚度。", 1, 0),
    ("底漆，厚度。", {("漆", "厚"): 149}, "底漆厚度。", 1, 1),
    ("，底漆,厚度、二十，", {}, "，底漆厚度二十，", 4, 2),
    ("厚度20,30。", {}, "厚度20,30。", 1, 0),
    ("一点二，牛米。", {}, "一点二牛米。", 1, 1),
    ("底漆，，厚度。", {}, "底漆厚度。", 2, 2),
    ("厚度，。二十。", {}, "厚度。二十。", 1, 1),
    ("A B 12 34，底 漆，厚 度。", {("34", "底"): 200},
     "A B 12 34，底 漆厚 度。", 2, 1),
])
def test_gate_commas(text, gaps, expected, commas, removed):
    corrected, stats = gate_commas(text, timestamps_for(text, gaps), 150)
    assert corrected == expected
    assert stats == {"aligned": True, "commas": commas, "removed": removed, "min_gap_ms": 150}


@pytest.mark.parametrize("timestamps", [None, [], [[0, 100]], [[0, 100]] * 20])
def test_unaligned_returns_original(timestamps):
    text = "底漆，厚度九十八。"
    assert gate_commas(text, timestamps, 150) == (
        text, {"aligned": False, "commas": 1, "removed": 0},
    )


def test_english_and_digits_are_single_tokens():
    text = "铭牌ZQ500，电机YZR160。"
    assert TOKEN.findall(text) == ["铭", "牌", "ZQ500", "，", "电", "机", "YZR160", "。"]
    assert gate_commas(text, [[i * 100, (i + 1) * 100] for i in range(6)], 150)[0] == (
        "铭牌ZQ500电机YZR160。"
    )


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_ENGINE", "fake")
    monkeypatch.setenv("ASR_DENOISE", "0")
    monkeypatch.setenv("ASR_FRONTEND_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("ASR_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ASR_PUNC_GATE", "1")
    monkeypatch.setenv("ASR_PUNC_GAP_MS", "150")


def engine_with_results(results):
    class Model:
        def generate(self, **kwargs):
            self.received = kwargs
            return results

    engine = FunAsrEngine()
    engine.model = Model()
    engine.loaded = True
    return engine


def test_engine_corrects_each_segment_and_preserves_join_rules():
    results = [
        {"text": "底 漆，厚 度。", "timestamp": timestamps_for("底 漆，厚 度。")},
        {"text": "铭牌 ZQ500，电机。", "timestamp": timestamps_for("铭牌 ZQ500，电机。")},
    ]
    engine = engine_with_results(results)
    detail = engine.transcribe_detail(Path("音频.wav"), ["底漆", "厚度"])
    assert detail == {
        "text": "底漆厚度。铭牌 ZQ500电机。",
        "text_model": "底漆，厚度。铭牌 ZQ500，电机。",
        "punc_gate": {"enabled": True, "aligned": True, "commas": 2, "removed": 2, "min_gap_ms": 150},
    }
    assert engine.model.received == {"input": "音频.wav", "hotword": "底漆 厚度", "batch_size_s": 300}
    assert engine.transcribe(Path("音频.wav"), []) == detail["text"]


def test_engine_partial_alignment():
    engine = engine_with_results([
        {"text": "底漆，厚度。", "timestamp": timestamps_for("底漆，厚度。")},
        {"text": "厚度，二十。"},
    ])
    detail = engine.transcribe_detail(Path("音频.wav"), [])
    assert detail["text"] == "底漆厚度。厚度，二十。"
    assert detail["punc_gate"] == {
        "enabled": True, "aligned": False, "commas": 2, "removed": 1, "min_gap_ms": 150,
    }


def test_engine_empty_results_and_not_loaded():
    engine = engine_with_results([])
    assert engine.transcribe_detail(Path("音频.wav"), []) == {
        "text": "", "text_model": "",
        "punc_gate": {"enabled": True, "aligned": False, "commas": 0, "removed": 0, "min_gap_ms": 150},
    }
    with pytest.raises(RuntimeError, match="识别模型尚未加载"):
        FunAsrEngine().transcribe_detail(Path("音频.wav"), [])


def test_fake_engine_detail(monkeypatch):
    monkeypatch.setenv("ASR_FAKE_TEXT", "底漆，厚度。")
    monkeypatch.setenv("ASR_PUNC_GATE", "0")
    monkeypatch.setenv("ASR_PUNC_GAP_MS", "200")
    assert FakeEngine().transcribe_detail(Path("音频.wav"), []) == {
        "text": "底漆，厚度。", "text_model": "底漆，厚度。",
        "punc_gate": {"enabled": False, "aligned": False, "commas": 0, "removed": 0, "min_gap_ms": 200},
    }


def test_config_defaults_and_override(monkeypatch):
    monkeypatch.delenv("ASR_PUNC_GATE")
    monkeypatch.delenv("ASR_PUNC_GAP_MS")
    assert get_config().punc_gate is True
    assert get_config().punc_gap_ms == 150
    monkeypatch.setenv("ASR_PUNC_GATE", "0")
    monkeypatch.setenv("ASR_PUNC_GAP_MS", "260")
    assert get_config().punc_gate is False
    assert get_config().punc_gap_ms == 260


@pytest.fixture
def service_audio(monkeypatch):
    # 这里仅验证识别结果的传递，转码行为由原服务测试覆盖。
    def convert(src, dst, max_seconds):
        dst.write_bytes(src.read_bytes())
        return 1.0

    monkeypatch.setattr(server, "to_wav16k", convert)
    monkeypatch.setattr(server, "_source_duration", lambda src: 1.0)
    return "音频占位".encode("utf-8")


@pytest.mark.parametrize("enabled", [True, False])
def test_service_detail_health_and_statistics_log(monkeypatch, tmp_path, service_audio, enabled):
    monkeypatch.setenv("ASR_PUNC_GATE", "1" if enabled else "0")
    monkeypatch.setenv("ASR_PUNC_GAP_MS", "180")
    original = "底漆，厚度九十八。"
    delegate = engine_with_results([{"text": original, "timestamp": timestamps_for(original)}])

    class DetailEngine:
        name = "fake"
        loaded = True

        def load(self):
            pass

        def transcribe_detail(self, wav, hotwords):
            return delegate.transcribe_detail(wav, hotwords)

        def transcribe(self, wav, hotwords):
            raise AssertionError("应优先调用详细识别接口")

    with TestClient(server.create_app(DetailEngine())) as client:
        response = client.post("/transcribe", files={"audio": ("sample.wav", service_audio, "audio/wav")})
        health = client.get("/health").json()
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == ("底漆厚度九十八。" if enabled else original)
    assert body["text_model"] == original
    assert body["punc_gate"] == {
        "enabled": enabled, "aligned": enabled, "commas": 1,
        "removed": 1 if enabled else 0, "min_gap_ms": 180,
    }
    assert body["duration_s"] == 1.0
    assert isinstance(body["elapsed_ms"], int)
    assert body["denoise"]["action"] == "off"
    assert body["truncated"] is False
    assert health["punc_gate"] == {"enabled": enabled, "min_gap_ms": 180}
    log = (tmp_path / "logs/asr.jsonl").read_text(encoding="utf-8")
    entry = json.loads(log)
    assert entry["punc_gate"] == body["punc_gate"]
    assert "text" not in entry
    assert "text_model" not in entry
    assert original not in log
    assert body["text"] not in log


def test_service_supports_legacy_engine(tmp_path, service_audio):
    class LegacyEngine:
        name = "legacy"
        loaded = True

        def load(self):
            pass

        def transcribe(self, wav, hotwords):
            return "底漆，厚度九十八。"

    with TestClient(server.create_app(LegacyEngine())) as client:
        response = client.post("/transcribe", files={"audio": ("sample.wav", service_audio, "audio/wav")})
    assert response.status_code == 200
    assert response.json()["text"] == response.json()["text_model"] == "底漆，厚度九十八。"
    assert response.json()["punc_gate"] is None
    assert json.loads((tmp_path / "logs/asr.jsonl").read_text())["punc_gate"] is None
