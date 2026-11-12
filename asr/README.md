独立音频识别服务，仅将音频转换为文字；运行环境需提供 ffmpeg（ffprobe 可选）。
仓库根目录启动：`.venv/bin/uvicorn asr.server:app --host 127.0.0.1 --port 8601`。
按环境变量地址启动：`.venv/bin/python -m asr.server`；ASR_HOST 默认 127.0.0.1，ASR_PORT 默认 8601。
ASR_ENGINE=funasr（默认）或 fake；ASR_FAKE_TEXT 默认「直径一千二百五十」；测试只用 fake。
ASR_DENOISE=0 默认关闭，1 开启（2026-11-12 实测见下）；ASR_MAX_SECONDS 默认 60；ASR_LOG_DIR 默认仓库下 logs。
ASR_FRONTEND_DIR 默认仓库下 asr/deepfilter（DeepFilterNet3），改为 ClearMic 等目录即可切换，相对路径环境变量均以仓库根目录解析。
ClearMic 接口：`asr_frontend.py` 提供 `process(in_wav: str, out_wav: str, vad: bool = False) -> dict`，始终传 vad=False。
前置模块须写出非空 wav，返回 action（bypass/light/strong）、snr_db（float）、applied（bool）；缺失或异常自动直通，模块按目录缓存，更新后重启。
GET /health 查看状态；POST /transcribe 上传 audio，可选 hotwords（空格分隔），省略时使用 hotwords.txt；请求后删除全部临时音频，仅记录决策和文字长度。
FunASR 首次启动需模型可用并预热；本任务测试不加载模型：`PYTHONPATH=. .venv/bin/python -m pytest -q tests/asr`。

## 按真实停顿校正标点
- 默认开启：`ASR_PUNC_GATE=1`；设为 `0` 关闭。`ASR_PUNC_GAP_MS` 默认 `150`，单位毫秒。
- 使用 Paraformer 的逐字时间戳，仅删除前后没有真实停顿（间隔严格小于阈值）、且没有隔开两个数字的逗号（`，`、`,`、`、`）。例如「控制线，力矩」可校正为「控制线力矩」；「九十五，一百」即使没有停顿也保留逗号。句首句尾的逗号及句号、问号等其他标点保留。
- 英文、阿拉伯数字连续串各算一个 token；时间戳为空或数量无法对齐时，该段保留原文。
- `/transcribe` 的 `text` 是校正后文本，`text_model` 是模型原始文本，`punc_gate` 含开关、是否全部段落对齐、原逗号数、删除数及阈值。旧识别器仅提供 `transcribe` 时，`text_model = text`、`punc_gate = null`。`/health` 返回开关与阈值；`asr.jsonl` 仅增加统计，不记录文字。
- 审核方实测：166 条真实录音中 14 条变好、0 条变差；24 句合成语音全对数由 14 提升至 19、0 条变差。这两组为审核方提供的既有验证结果。

## DeepFilterNet 降噪（asr/deepfilter）
- 流程：上传录音 → 转 48kHz → DeepFilterNet3 → 转 16kHz → FunASR；失败、超时、进程崩溃、模型未就绪时自动用未降噪的 16kHz 音频，原始上传文件和转码文件都不会被覆盖。
- 运行在独立环境 `.venv-df`（Python 3.11、torch 2.0.1、deepfilternet 0.5.6、numpy<2；与 `.venv` 不兼容，不能合并）：`sh asr/deepfilter/setup_env.sh` 安装并预下载模型（约 8MB）。
- 服务启动时后台拉起常驻子进程 `worker.py`，模型只加载一次；`/health` 的 `denoiser` 显示 ready、load_ms、starts、error。
- 环境变量：ASR_DF_PYTHON、ASR_DF_WORKER、ASR_DF_TIMEOUT（30）、ASR_DF_START_TIMEOUT（120）、ASR_DF_ATTEN_LIM_DB（默认 6；off 不限）、ASR_DF_BYPASS_SNR_DB（默认不跳过）。
- 对比：`.venv/bin/python tools/asr_bench.py`（合成语料 + motor/impact/grind/fan 噪声，可加 `--real DIR`），报告写到 `logs/asr-bench`。
- 2026-11-12 实测（合成语音 + 合成噪声，24 句）：不限衰减时宽频噪声下明显变差（打磨 -5 dB 整句 11→3）；限 6 dB 基本持平（干净录音不变，字错率略降，整句 ±1～2）；40 条真实手机录音 14 条文字改变，有好有坏；每条多约 0.23 秒。因此默认关闭，拿到真实车间噪声录音验证有提升后再开启。
