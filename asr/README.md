独立音频识别服务，仅将音频转换为文字；运行环境需提供 ffmpeg（ffprobe 可选）。
仓库根目录启动：`.venv/bin/uvicorn asr.server:app --host 127.0.0.1 --port 8601`。
按环境变量地址启动：`.venv/bin/python -m asr.server`；ASR_HOST 默认 127.0.0.1，ASR_PORT 默认 8601。
ASR_ENGINE=funasr（默认）或 fake；ASR_FAKE_TEXT 默认「直径一千二百五十」；测试只用 fake。
ASR_DENOISE=1 默认开启，0 关闭；ASR_MAX_SECONDS 默认 60；ASR_LOG_DIR 默认仓库下 logs。
ASR_FRONTEND_DIR 默认仓库下 asr/vendor/clearmic，相对路径环境变量均以仓库根目录解析。
ClearMic 接口：`asr_frontend.py` 提供 `process(in_wav: str, out_wav: str, vad: bool = False) -> dict`，始终传 vad=False。
前置模块须写出非空 wav，返回 action（bypass/light/strong）、snr_db（float）、applied（bool）；缺失或异常自动直通，模块按目录缓存，更新后重启。
GET /health 查看状态；POST /transcribe 上传 audio，可选 hotwords（空格分隔），省略时使用 hotwords.txt；请求后删除全部临时音频，仅记录决策和文字长度。
FunASR 首次启动需模型可用并预热；本任务测试不加载模型：`PYTHONPATH=. .venv/bin/python -m pytest -q tests/asr`。
