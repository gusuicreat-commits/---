import re
from pathlib import Path

import numpy as np

from asr.config import get_config


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
        if not self.loaded:
            raise RuntimeError("识别模型尚未加载")
        results = self.model.generate(
            input=str(wav), hotword=" ".join(hotwords), batch_size_s=300,
        )
        return join_text(results)


class FakeEngine:
    name = "fake"
    loaded = True

    def load(self):
        pass

    def transcribe(self, wav: Path, hotwords: list[str]) -> str:
        return get_config().fake_text


def make_engine(kind: str):
    if kind == "funasr":
        return FunAsrEngine()
    if kind == "fake":
        return FakeEngine()
    raise ValueError(f"不支持的识别引擎：{kind}")
