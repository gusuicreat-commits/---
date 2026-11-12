"""使用真实 FunASR 与独立降噪进程进行可复现的双路对比。"""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import soundfile

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.voice.parser import parse_text
from asr import denoise
from asr.engine import FunAsrEngine
from asr.transcode import to_wav, to_wav16k

FRONTEND = ROOT_DIR / "asr/deepfilter"
RATE = 48000
SEED = 20261112

# 期望值独立于识别结果；启动时先用原句验证当前解析契约。
CORPUS = [
    ("drum", "直径一千二百五十，长度三千", {"diameter": "1250", "length": "3000"}),
    ("drum", "厚度二十毫米，光段三百一十", {"thick": "20", "light": "310"}),
    ("drum", "直径一千二百六十二", {"diameter": "1262"}),
    ("drum", "直径一千二百五十毫米，厚度二十", {"diameter": "1250", "thick": "20"}),
    ("drum", "长度三千毫米，光段三百", {"length": "3000", "light": "300"}),
    ("drum", "直径一千二百五十，长度三千，厚度二十，光段三百", {"diameter": "1250", "length": "3000", "thick": "20", "light": "300"}),
    ("travel_drive", "减速机铭牌 ZQ500，电机铭牌 YZR160", {"reducer_nameplate": "ZQ500", "motor_nameplate": "YZR160"}),
    ("travel_drive", "噪声八十二分贝", {"noise": "82"}),
    ("travel_drive", "噪音七十九点五", {"noise": "79.5"}),
    ("travel_drive", "减速机铭牌 ZQ500", {"reducer_nameplate": "ZQ500"}),
    ("travel_drive", "电机铭牌 YZR160", {"motor_nameplate": "YZR160"}),
    ("travel_drive", "减速机铭牌 ZQ500，噪声八十", {"reducer_nameplate": "ZQ500", "noise": "80"}),
    ("control_cabinet", "绝缘电阻五百兆欧，控制线力矩一点二牛米", {"insulation_resistance": "500", "control_wire_torque": "1.2"}),
    ("control_cabinet", "电气间隙八毫米", {"electrical_clearance": "8"}),
    ("control_cabinet", "绝缘电阻五百兆欧", {"insulation_resistance": "500"}),
    ("control_cabinet", "控制线力矩一点二牛米", {"control_wire_torque": "1.2"}),
    ("control_cabinet", "绝缘电阻六百兆欧，电气间隙十毫米", {"insulation_resistance": "600", "electrical_clearance": "10"}),
    ("control_cabinet", "绝缘电阻五百兆欧，控制线力矩一点二牛米，电气间隙八毫米", {"insulation_resistance": "500", "control_wire_torque": "1.2", "electrical_clearance": "8"}),
    ("travel_paint", "底漆厚度九十八，面漆厚度九十九", {"primer_thickness_ratio": "98", "total_thickness_ratio": "99"}),
    ("travel_paint", "附着力百分之一", {"adhesion_loss_ratio": "1"}),
    ("travel_paint", "底漆厚度九十八", {"primer_thickness_ratio": "98"}),
    ("travel_paint", "面漆厚度九十九", {"total_thickness_ratio": "99"}),
    ("travel_paint", "底漆厚度九十八，面漆厚度九十九，附着力百分之一", {"primer_thickness_ratio": "98", "total_thickness_ratio": "99", "adhesion_loss_ratio": "1"}),
    ("travel_paint", "底漆厚度九十七，面漆厚度九十八，附着力百分之二", {"primer_thickness_ratio": "97", "total_thickness_ratio": "98", "adhesion_loss_ratio": "2"}),
]


def _path(value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT_DIR / path).resolve()


def validate_corpus(today):
    for index, (form, text, expected) in enumerate(CORPUS, 1):
        actual = parse_text(text, form, today)["fields"]
        if actual != expected:
            raise ValueError(f"第 {index} 句语料期望字段不成立：{actual}，期望 {expected}")


def normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text)
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def _unit(signal):
    signal = signal - np.mean(signal)
    return signal / max(float(np.sqrt(np.mean(signal ** 2))), 1e-12)


def make_noise(kind, size, rng):
    t = np.arange(size) / RATE
    if kind == "motor":
        hum = sum(np.sin(2 * np.pi * frequency * t + rng.uniform(0, 2 * np.pi)) / (index + 1)
                  for index, frequency in enumerate((50, 100, 150, 300)))
        brown = np.cumsum(rng.standard_normal(size))
        return _unit(_unit(hum) + 0.5 * _unit(brown))
    if kind == "impact":
        frequencies = np.fft.rfftfreq(size, 1 / RATE)
        spectrum = np.fft.rfft(rng.standard_normal(size))
        spectrum /= np.sqrt(np.maximum(frequencies, 1))
        pink = _unit(np.fft.irfft(spectrum, n=size))
        knocks = np.zeros(size)
        position = int(rng.uniform(0.3, 0.8) * RATE)
        while position < size:
            width = min(int(0.04 * RATE), size - position)
            envelope = np.exp(-np.arange(width) / (0.006 * RATE))
            knocks[position:position + width] += envelope * rng.standard_normal(width) * 10
            position += int(rng.uniform(0.3, 0.8) * RATE)
        return _unit(pink + knocks)
    if kind == "grind":
        # 打磨/切割：1～7kHz 宽带噪声，叠加 4～9Hz 的起伏
        frequencies = np.fft.rfftfreq(size, 1 / RATE)
        spectrum = np.fft.rfft(rng.standard_normal(size))
        spectrum[(frequencies < 1000) | (frequencies > 7000)] *= 0.1
        band = _unit(np.fft.irfft(spectrum, n=size))
        wobble = 1 + 0.5 * np.sin(2 * np.pi * rng.uniform(4, 9) * t)
        return _unit(band * wobble)
    if kind == "fan":
        # 风机/气泵：白噪声为主，带 120Hz 嗡声
        return _unit(rng.standard_normal(size) + 0.3 * np.sin(2 * np.pi * 120 * t))
    raise ValueError(f"不支持的噪声类型：{kind}")


def mix_noise(voice, kind, snr, seed):
    rng = np.random.default_rng(seed)
    noise = make_noise(kind, len(voice), rng)
    gain = math.sqrt(float(np.mean(voice ** 2)) / (10 ** (snr / 10)))
    mixed = voice + noise * gain
    # 整段等比例缩放，保留信噪比并避免转成 PCM 时削波。
    peak = float(np.max(np.abs(mixed)))
    return (mixed * min(1.0, 0.98 / max(peak, 1e-12))).astype(np.float32)


def synthesize(index, text, corpus_dir, work_dir, max_seconds):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    target = corpus_dir / f"{index:02d}-{digest}.wav"
    if target.is_file():
        info = soundfile.info(target)
        if info.frames and info.samplerate == RATE and info.channels == 1:
            return target
    executable = shutil.which("say")
    if executable is None:
        raise RuntimeError("找不到 macOS say，无法合成测试语料")
    aiff = work_dir / f"{index:02d}.aiff"
    result = subprocess.run([executable, "-v", "Tingting", "-o", str(aiff), text],
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("语音合成失败：" + result.stderr.decode("utf-8", errors="replace")[-200:])
    to_wav(aiff, target, max_seconds, RATE)
    aiff.unlink(missing_ok=True)
    return target


def run_path(source, route, engine, hotwords, work_dir, max_seconds):
    started = time.perf_counter()
    wav16, wav48, enhanced = (work_dir / name for name in ("converted.wav", "converted48k.wav", "denoised.wav"))
    duration = to_wav16k(source, wav16, max_seconds)
    selected = wav16
    decision = None
    denoise_ms = 0.0
    if route == "denoised":
        to_wav(source, wav48, max_seconds, RATE)
        step = time.perf_counter()
        selected, decision = denoise.apply(wav16, enhanced, True, FRONTEND, src48=wav48)
        denoise_ms = (time.perf_counter() - step) * 1000
    step = time.perf_counter()
    text = engine.transcribe(selected, hotwords)
    recognition_ms = (time.perf_counter() - step) * 1000
    total_ms = (time.perf_counter() - started) * 1000
    return {"route": route, "text": text, "duration_s": duration, "decision": decision,
            "denoise_ms": denoise_ms, "recognition_ms": recognition_ms, "total_ms": total_ms,
            "rtf": total_ms / 1000 / max(duration, 1e-10)}


def summarize(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[(record["noise"], record["snr"], record["route"])].append(record)
    summaries = []
    for (noise, snr, route), items in grouped.items():
        fields = defaultdict(lambda: {"correct": 0, "total": 0})
        for item in items:
            for name, correct in item["field_matches"].items():
                fields[name]["correct"] += int(correct)
                fields[name]["total"] += 1
        for value in fields.values():
            value["accuracy"] = value["correct"] / value["total"]
        stats = {}
        for metric in ("denoise_ms", "recognition_ms", "total_ms", "rtf"):
            values = [item[metric] for item in items]
            stats[metric] = {"mean": float(np.mean(values)), "p95": float(np.percentile(values, 95))}
        summaries.append({"noise": noise, "snr": snr, "route": route, "count": len(items),
                          "cer": sum(item["edits"] for item in items) / max(1, sum(item["reference_chars"] for item in items)),
                          "sentence_accuracy": sum(item["fields_correct"] for item in items) / len(items),
                          "field_accuracy": sum(value["correct"] for value in fields.values()) / max(1, sum(value["total"] for value in fields.values())),
                          "per_field": dict(fields), "timing": stats,
                          "rtf": sum(item["total_ms"] for item in items) / 1000 / max(1e-10, sum(item["duration_s"] for item in items)),
                          "denoise_errors": sum(item["decision"] is not None and item["decision"]["action"] == "error" for item in items)})
    return summaries


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown(report):
    lines = ["# 识别降噪双路对比", "", f"北京时间：{report['timestamp']}", "",
             "合成语音与人工噪声用于可复现对比；真实录音没有参考答案，不计算准确率。",
             "字段整句正确率要求解析字段与期望完全一致；逐字段正确率按期望字段分别统计。",
             "总耗时包括转码、降噪和识别，不包括一次性模型加载与语料合成。", "",
             "| 噪声 | 信噪比 | 路径 | 样本数 | 字错率 | 字段整句正确率 | 逐字段正确率 | 降噪失败数 | 实时率 |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for group in report["groups"]:
        route = "原始" if group["route"] == "raw" else "降噪"
        lines.append(f"| {group['noise']} | {group['snr']} | {route} | {group['count']} | {group['cer']:.2%} | {group['sentence_accuracy']:.2%} | {group['field_accuracy']:.2%} | {group['denoise_errors']} | {group['rtf']:.3f} |")
    lines += ["", "| 噪声 | 信噪比 | 路径 | 降噪平均 / P95（毫秒） | 识别平均 / P95（毫秒） | 总计平均 / P95（毫秒） |", "| --- | --- | --- | ---: | ---: | ---: |"]
    for group in report["groups"]:
        values = [f"{group['timing'][key]['mean']:.1f} / {group['timing'][key]['p95']:.1f}"
                  for key in ("denoise_ms", "recognition_ms", "total_ms")]
        route = "原始" if group["route"] == "raw" else "降噪"
        lines.append(f"| {group['noise']} | {group['snr']} | {route} | " + " | ".join(values) + " |")
    lines += ["", "| 噪声 | 信噪比 | 路径 | 字段 | 正确 / 总数 | 正确率 |", "| --- | --- | --- | --- | ---: | ---: |"]
    for group in report["groups"]:
        for name, value in group["per_field"].items():
            route = "原始" if group["route"] == "raw" else "降噪"
            lines.append(f"| {group['noise']} | {group['snr']} | {route} | {name} | {value['correct']} / {value['total']} | {value['accuracy']:.2%} |")
    lines += ["", "| 真实录音 | 原始文字 | 降噪文字 | 文字一致 | 原始总耗时（毫秒） | 降噪总耗时（毫秒） | 降噪耗时（毫秒） | 原始 / 降噪识别耗时（毫秒） |", "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for item in report["real"]:
        raw, enhanced = item["raw"], item["denoised"]
        lines.append(f"| {_cell(item['file'])} | {_cell(raw['text'])} | {_cell(enhanced['text'])} | {'是' if item['same_text'] else '否'} | {raw['total_ms']:.1f} | {enhanced['total_ms']:.1f} | {enhanced['denoise_ms']:.1f} | {raw['recognition_ms']:.1f} / {enhanced['recognition_ms']:.1f} |")
    if report["errors"]:
        lines += ["", "## 未完成样本", "", "| 样本 | 错误 |", "| --- | --- |"]
        lines += [f"| {_cell(item['sample'])} | {_cell(item['error'])} |" for item in report["errors"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="对比原始音频与 DeepFilterNet 降噪后的识别结果", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("--out", default=os.environ.get("ASR_BENCH_OUT", "logs/asr-bench"), help="输出目录")
    parser.add_argument("--snr", default="clean,15,5,0", help="逗号分隔的信噪比，clean 表示不叠加噪声")
    parser.add_argument("--noise", default="motor,impact", help="逗号分隔的噪声类型")
    parser.add_argument("--real", help="只读的真实录音目录")
    parser.add_argument("--limit", type=int, help="合成语料与真实录音各自的数量上限")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("数量上限必须大于零")
    noises = list(dict.fromkeys(part.strip() for part in args.noise.split(",")))
    if not noises or any(value not in {"motor", "impact", "grind", "fan"} for value in noises):
        parser.error("噪声类型仅支持 motor、impact")
    try:
        snrs = list(dict.fromkeys("clean" if value.strip() == "clean" else float(value) for value in args.snr.split(",")))
        if any(value != "clean" and not math.isfinite(value) for value in snrs):
            raise ValueError
    except ValueError:
        parser.error("信噪比必须为 clean 或有限数字")
    real_dir = _path(args.real) if args.real else None
    out = _path(args.out)
    if real_dir is not None:
        if not real_dir.is_dir():
            parser.error("真实录音目录不存在")
        if out.is_relative_to(real_dir):
            parser.error("输出目录不能位于只读的真实录音目录内")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    validate_corpus(now.date())
    out.mkdir(parents=True, exist_ok=True)
    corpus_dir = out / "corpus"
    corpus_dir.mkdir(exist_ok=True)
    report = {"timestamp": now.isoformat(), "seed": SEED, "corpus": [], "samples": [], "groups": [],
              "real": [], "errors": [], "denoiser": None}
    old_log_dir = os.environ.get("ASR_LOG_DIR")
    os.environ["ASR_LOG_DIR"] = str(out / "logs")
    try:
        state = denoise.warmup(FRONTEND)
        report["denoiser"] = state
        if not state or not state["ready"]:
            raise RuntimeError(f"降噪模型未就绪：{state}")
        engine = FunAsrEngine()
        engine.load()
        hotwords = (ROOT_DIR / "asr/hotwords.txt").read_text(encoding="utf-8").split()
        max_seconds = int(os.environ.get("ASR_MAX_SECONDS", "60"))
        with tempfile.TemporaryDirectory(prefix="work-", dir=out) as temporary:
            work_dir = Path(temporary)
            for index, (form, reference, expected) in enumerate(CORPUS[:args.limit], 1):
                source = synthesize(index, reference, corpus_dir, work_dir, max_seconds)
                report["corpus"].append({"id": index, "form": form, "text": reference, "expected": expected})
                voice, _ = soundfile.read(source, dtype="float32")
                for noise in noises:
                    for snr_index, snr in enumerate(snrs):
                        sample = work_dir / "sample.wav"
                        if snr == "clean":
                            shutil.copyfile(source, sample)
                        else:
                            signal = mix_noise(voice, noise, snr, SEED + index * 100 + snr_index * 10 + noises.index(noise))
                            soundfile.write(sample, signal, RATE, subtype="FLOAT")
                        for route in ("raw", "denoised"):
                            key = f"{index}/{noise}/{snr}/{route}"
                            try:
                                result = run_path(sample, route, engine, hotwords, work_dir, max_seconds)
                                fields = parse_text(result["text"], form, now.date())["fields"]
                                ref, hyp = normalized(reference), normalized(result["text"])
                                result.update(id=index, form=form, reference=reference, expected=expected,
                                              fields=fields, noise=noise, snr=snr,
                                              reference_chars=len(ref), edits=edit_distance(ref, hyp),
                                              fields_correct=fields == expected,
                                              field_matches={name: fields.get(name) == value for name, value in expected.items()})
                                report["samples"].append(result)
                            except Exception as exc:
                                report["errors"].append({"sample": key, "error": f"样本处理失败：{exc}"[:300]})
            if real_dir is not None:
                extensions = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aiff", ".aac", ".mp4", ".opus"}
                files = sorted(path for path in real_dir.iterdir() if path.is_file() and path.suffix.lower() in extensions)
                for source in files[:args.limit]:
                    try:
                        raw = run_path(source, "raw", engine, hotwords, work_dir, max_seconds)
                        enhanced = run_path(source, "denoised", engine, hotwords, work_dir, max_seconds)
                        report["real"].append({"file": str(source), "raw": raw, "denoised": enhanced,
                                               "same_text": raw["text"] == enhanced["text"]})
                    except Exception as exc:
                        report["errors"].append({"sample": str(source), "error": f"真实录音处理失败：{exc}"[:300]})
    finally:
        denoise.shutdown(FRONTEND)
        if old_log_dir is None:
            os.environ.pop("ASR_LOG_DIR", None)
        else:
            os.environ["ASR_LOG_DIR"] = old_log_dir
    report["groups"] = summarize(report["samples"])
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    target = out / f"report-{stamp}"
    target.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    content = markdown(report)
    target.with_suffix(".md").write_text(content, encoding="utf-8")
    print(content)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"基准测试失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
