"""无需模型的协议替身，故障模式由测试环境变量控制。"""

import json
import os
import sys
import time
from pathlib import Path

import soundfile
from scipy.signal import resample_poly
from math import gcd


def reply(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    mode = os.environ.get("FAKE_DF_MODE", "success")
    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_DF_DELAY", "1.2")))
    if mode == "ready_error":
        reply({"ready": False, "error": "模拟降噪模型加载失败"})
        return 1
    reply({"ready": True, "model": "DeepFilterNet3", "sr": 48000, "load_ms": 8})
    for line in sys.stdin:
        request = json.loads(line)
        marker = Path(os.environ["FAKE_DF_MARKER"]) if mode.endswith("_once") else None
        fail_once = marker is not None and not marker.exists()
        if fail_once:
            marker.write_text("已触发故障", encoding="utf-8")
        if mode == "timeout" or (mode == "timeout_once" and fail_once):
            time.sleep(60)
            continue
        if mode == "crash_once" and fail_once:
            return 1
        if mode == "ok_error":
            reply({"id": request["id"], "ok": False, "error": "模拟降噪处理失败"})
            continue
        samples, rate = soundfile.read(request["in"], always_2d=True, dtype="float32")
        capture = os.environ.get("FAKE_DF_CAPTURE")
        if capture:
            with Path(capture).open("a", encoding="utf-8") as output:
                output.write(json.dumps({**request, "in_sr": rate}) + "\n")
        signal = samples.mean(axis=1)
        factor = gcd(rate, 16000)
        signal = resample_poly(signal, 16000 // factor, rate // factor)
        soundfile.write(request["out"], signal, 16000, format="WAV", subtype="PCM_16")
        reply({"id": request["id"], "ok": True, "action": "light", "applied": True,
               "snr_db": 23.4, "in_sr": rate, "elapsed_ms": 2})
        if mode == "after_reply_once" and fail_once:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
