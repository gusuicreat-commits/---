import re
from pathlib import Path

import numpy as np

from asr.config import get_config
from asr.punctuation import COMMAS, gate_commas


def join_text(results: list[dict]) -> str:
    text = "".join(result.get("text", "") for result in results)
    return re.sub(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3400-\u4dbf\u4e00-\u9fff])", "", text)


class FunAsrEngine:
    name = "funasr"

    def __init__(self):
        self.model = None
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        from funasr import AutoModel

        self.model = AutoModel(
            model="paraformer-zh", vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            punc_model="ct-punc", device="cpu", disable_update=True,
        )
        self.model.generate(input=np.zeros(16000, dtype=np.float32))
        self.loaded = True

    def transcribe(self, wav: Path, hotwords: list[str]) -> str:
        return self.transcribe_detail(wav, hotwords)["text"]

    def transcribe_detail(self, wav: Path, hotwords: list[str]) -> dict:
        if not self.loaded:
            raise RuntimeError("识别模型尚未加载")
        results = self.model.generate(
            input=str(wav), hotword=" ".join(hotwords), batch_size_s=300,
        )
        config = get_config()
        corrected = []
        stats = {
            "enabled": config.punc_gate, "aligned": bool(results) and config.punc_gate,
            "commas": 0, "removed": 0, "min_gap_ms": config.punc_gap_ms,
        }
        for item in results:
            text = item.get("text", "")
            if config.punc_gate:
                text, item_stats = gate_commas(text, item.get("timestamp"), config.punc_gap_ms)
                stats["aligned"] = stats["aligned"] and item_stats["aligned"]
                stats["commas"] += item_stats["commas"]
                stats["removed"] += item_stats["removed"]
            else:
                stats["commas"] += sum(char in COMMAS for char in text)
            corrected.append({"text": text})
        return {"text": join_text(corrected), "text_model": join_text(results), "punc_gate": stats}


class FakeEngine:
    name = "fake"
    loaded = True

    def load(self):
        pass

    def transcribe(self, wav: Path, hotwords: list[str]) -> str:
        return get_config().fake_text

    def transcribe_detail(self, wav: Path, hotwords: list[str]) -> dict:
        config = get_config()
        text = self.transcribe(wav, hotwords)
        return {
            "text": text, "text_model": text,
            "punc_gate": {
                "enabled": config.punc_gate, "aligned": False, "commas": 0,
                "removed": 0, "min_gap_ms": config.punc_gap_ms,
            },
        }


def make_engine(kind: str):
    if kind == "funasr":
        return FunAsrEngine()
    if kind == "fake":
        return FakeEngine()
    raise ValueError(f"不支持的识别引擎：{kind}")
