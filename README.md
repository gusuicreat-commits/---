# 质检资料一体化

起重机出厂检验的参数录入与校验系统。质检员对着手机说出测量值，系统自动识别并填表，按规则判定合格与否，大屏和手机实时同步，最后一键导出 Excel 检验汇总。

## 功能

- **合同与检验**：合同号格式为「年份 + 随机四位」。每个合同包含四类检验：卷筒、大车传动、控制柜、大车油漆，共 13 个字段。
- **规则校验**：
  - 每个字段按 min–max 判定合格与否，规则全局一套，可在管理后台修改；
  - 底漆支持多个测点，逐值校验；
  - 合同状态分为 合格 / 不合格 / 未录全 / 待校验。
- **语音填表**：
  - 识别引擎是 FunASR，以独立服务运行；
  - 解析器支持热词、纠错表、拼音同音匹配和河南话口音，并能处理逗号插在参数名中间、参数名和数值分在两段等情况；
  - 铭牌型号会自动规整，例如「zq五百」→「ZQ500」。
- **证据追溯**：每次提交都关联对应的语音和原始录音。人工复核的纠正会被记住，用于以后的识别纠错。
- **三个界面**：大屏驾驶舱 `screen.html`、质检员手机端 `phone.html`、管理后台 `admin.html`。前两者通过 SSE 实时联动。
- **一键演示**：18 步河南话演示流程，中间可以插入现场的真实录音。
- **导出**：单页「检验汇总」Excel。

## 结构

```
app/      主服务（FastAPI + SQLite）：接口、校验、导出、语音解析（app/voice/）
asr/      识别服务（FunASR）：转码、可选降噪、按停顿校正标点，详见 asr/README.md
web/      前端页面（无构建步骤，直接由主服务提供）
config/   字段定义、语音关键词与纠错表、演示脚本
tests/    单元与接口测试
tools/    HTTPS 启动脚本、识别对比脚本 asr_bench.py、浏览器全流程测试 e2e/
```

## 运行

需要 Python 3.12 和 ffmpeg。

```bash
uv venv .venv && uv pip install -r requirements.txt -r asr/requirements.txt

# 识别服务（模型首次加载约 30 秒）
.venv/bin/uvicorn asr.server:app --host 127.0.0.1 --port 8601

# 主服务：本机访问
.venv/bin/uvicorn app.main:app --port 8600
# 主服务：手机要用麦克风必须走 https，脚本会生成自签证书并打印大屏和手机地址
tools/run_https.sh
```

打开 `/screen.html` 进入大屏，`/phone.html` 进入手机端，`/admin.html` 进入管理后台。

### 主服务环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QC_DB_PATH` | `data/qc.sqlite3` | 数据库 |
| `QC_AUDIO_DIR` | `data/audio` | 原始录音存档 |
| `QC_EXPORT_DIR` | `exports` | Excel 导出目录 |
| `QC_ASR_URL` | `http://127.0.0.1:8601` | 识别服务地址 |

### 识别服务环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ASR_PUNC_GATE` | `1` | 按真实停顿去掉多余逗号 |
| `ASR_PUNC_GAP_MS` | `150` | 停顿阈值，单位毫秒 |
| `ASR_DENOISE` | `0` | DeepFilterNet3 降噪，默认关闭；需要单独的 `.venv-df` 环境 |

## 测试

```bash
.venv/bin/python -m pytest -q        # 单元与接口测试，识别服务用假引擎，不加载模型
.venv/bin/python tools/asr_bench.py  # 识别准确率与降噪对比（合成语音 + 合成噪声）
```

`tools/e2e/` 下是 Playwright 浏览器全流程测试。运行时用 `QC_BASE` 指向一个使用临时数据库副本的测试服务，不要指向正式库。

## 已知限制

- 没有真实车间噪声的录音，降噪的实际效果尚未验证，因此默认关闭。
- 浏览器测试 `extended` 在等待真实识别结果时偶尔超时。
- 浏览器测试里的合格判定按测试编写时的规则写死。管理后台改了规则后，相关断言可能失败。
