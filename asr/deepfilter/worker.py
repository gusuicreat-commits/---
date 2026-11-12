"""独立降噪进程，标准输出仅用于逐行 JSON 协议。"""

import contextlib
import json
import math
import sys
import time
import warnings
from pathlib import Path


def reply(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    warnings.showwarning = lambda message, category, filename, lineno, file=None, line=None: sys.stderr.write(
        f"警告：{message}\n"
    )
    started = time.perf_counter()
    try:
        # 第三方导入、模型加载及推理的打印也必须避开协议输出。
        with contextlib.redirect_stdout(sys.stderr):
            import soundfile
            import torch
            import torchaudio
            from df.enhance import init_df, enhance
            from loguru import logger

            logger.remove()
            logger.add(sys.stderr)
            # 关闭库默认的缓存目录日志文件，所有日志统一由父进程收集。
            model, state, _ = init_df(log_file=None)
        reply({"ready": True, "model": "DeepFilterNet3", "sr": 48000,
               "load_ms": int((time.perf_counter() - started) * 1000)})
    except Exception as exc:
        reply({"ready": False, "error": f"降噪模型加载失败：{exc}"[:200]})
        return 1

    for line in sys.stdin:
        request_id = None
        started = time.perf_counter()
        try:
            request = json.loads(line)
            request_id = request["id"]
            source, target = Path(request["in"]), Path(request["out"])
            if source.resolve() == target.resolve() or (target.exists() and source.samefile(target)):
                raise ValueError("降噪输出不能覆盖原始录音")
            with contextlib.redirect_stdout(sys.stderr), torch.no_grad():
                samples, in_sr = soundfile.read(str(source), dtype="float32", always_2d=True)
                if len(samples) == 0:
                    raise ValueError("输入音频为空")
                x = torch.from_numpy(samples.mean(axis=1)).unsqueeze(0)
                x48 = x if in_sr == 48000 else torchaudio.functional.resample(x, in_sr, 48000)
                enhanced = enhance(model, state, x48, atten_lim_db=request.get("atten_lim_db"))
                original16 = x if in_sr == 16000 else torchaudio.functional.resample(x, in_sr, 16000)
                enhanced16 = torchaudio.functional.resample(enhanced, 48000, 16000)
                size = min(original16.shape[-1], enhanced16.shape[-1])
                if size == 0:
                    raise ValueError("降噪输出为空")
                original16, enhanced16 = original16[..., :size], enhanced16[..., :size]
                noise_energy = max(float(((original16 - enhanced16) ** 2).sum()), 1e-10)
                signal_energy = float((enhanced16 ** 2).sum())
                snr = round(max(-20.0, min(60.0, 10 * math.log10(max(signal_energy, 1e-30) / noise_energy))), 1)
                threshold = request.get("bypass_snr_db")
                bypass = threshold is not None and snr >= threshold
                action = "bypass" if bypass else ("light" if snr >= 20 else "strong")
                output = original16 if bypass else enhanced16
                soundfile.write(str(target), output.squeeze(0).cpu().numpy(), 16000,
                                format="WAV", subtype="PCM_16")
            reply({"id": request_id, "ok": True, "action": action, "applied": not bypass,
                   "snr_db": snr, "in_sr": in_sr,
                   "elapsed_ms": int((time.perf_counter() - started) * 1000)})
        except Exception as exc:
            reply({"id": request_id, "ok": False, "error": f"降噪处理失败：{exc}"[:200]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
