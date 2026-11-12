"""验证基准脚本的指标与产物，不运行真实识别模型。"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
import soundfile

from asr import denoise

ROOT_DIR = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("asr_bench", ROOT_DIR / "tools/asr_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_corpus_and_metrics():
    bench.validate_corpus(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    assert len(bench.CORPUS) == 24
    assert {form: sum(item[0] == form for item in bench.CORPUS) for form, _, _ in bench.CORPUS} == {
        "drum": 6, "travel_drive": 6, "control_cabinet": 6, "travel_paint": 6,
    }
    assert bench.edit_distance("直径1250", "直经125") == 2
    assert bench.normalized("直径 １２５０，\n长度。") == "直径1250长度"
    record = {"noise": "motor", "snr": 5, "route": "raw", "field_matches": {"diameter": True},
              "fields_correct": True, "edits": 2, "reference_chars": 8,
              "denoise_ms": 0, "recognition_ms": 100, "total_ms": 200, "rtf": 0.2,
              "duration_s": 1, "decision": None}
    group = bench.summarize([record, {**record, "fields_correct": False, "field_matches": {"diameter": False}, "edits": 0}])[0]
    assert group["cer"] == 0.125
    assert group["sentence_accuracy"] == group["field_accuracy"] == 0.5
    assert group["rtf"] == 0.2
    assert group["timing"]["recognition_ms"] == {"mean": 100, "p95": 100}


@pytest.mark.parametrize("noise", ["motor", "impact"])
def test_noise_is_reproducible_at_requested_snr(noise):
    voice = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000) * 0.03
    mixed = bench.mix_noise(voice, noise, 5, 10)
    repeated = bench.mix_noise(voice, noise, 5, 10)
    assert np.array_equal(mixed, repeated)
    measured = 10 * np.log10(np.sum(voice ** 2) / np.sum((mixed - voice) ** 2))
    assert measured == pytest.approx(5, abs=0.01)
    assert np.isfinite(mixed).all()


def test_report_and_real_directory_are_read_only(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output"
    recordings = tmp_path / "real"
    recordings.mkdir()
    original = recordings / "sample.wav"
    soundfile.write(original, np.zeros(1600), 16000)
    before = original.read_bytes()
    calls = []
    model_loads = []
    shutdowns = []

    class Engine:
        def load(self):
            model_loads.append(True)

    def synthesize(index, text, corpus_dir, work_dir, max_seconds):
        target = corpus_dir / "sample.wav"
        soundfile.write(target, np.zeros(48000), 48000)
        return target

    def run_path(source, route, engine, hotwords, work_dir, max_seconds):
        calls.append((source, route))
        assert work_dir.is_relative_to(output)
        return {"route": route, "text": bench.CORPUS[0][1], "duration_s": 1,
                "denoise_ms": 0 if route == "raw" else 5, "recognition_ms": 10,
                "total_ms": 20, "rtf": 0.02, "decision": None}

    monkeypatch.setattr(bench, "FunAsrEngine", Engine)
    monkeypatch.setattr(bench, "synthesize", synthesize)
    monkeypatch.setattr(bench, "run_path", run_path)
    monkeypatch.setattr(denoise, "warmup", lambda path: {"ready": True})
    monkeypatch.setattr(denoise, "shutdown", lambda path: shutdowns.append(path))
    assert bench.main(["--out", str(output), "--snr", "clean", "--noise", "motor",
                       "--real", str(recordings), "--limit", "1"]) == 0
    reports = list(output.glob("report-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["timestamp"].endswith("+08:00")
    assert len(report["samples"]) == 2 and len(report["real"]) == 1
    assert report["real"][0]["same_text"] is True
    assert len(calls) == 4 and len(model_loads) == len(shutdowns) == 1
    assert "字错率" in capsys.readouterr().out
    assert reports[0].with_suffix(".md").is_file()
    assert original.read_bytes() == before
    assert list(recordings.iterdir()) == [original]
    assert not list(output.glob("work-*"))
