# 交接文档 · 产品参数校验系统

> 写于 2026-11-10 13:40（北京时间）。写给下一个接手的 agent。
> 先读本文件，再读 `产品参数校验系统设计方案.md`（v7，需求的唯一来源）。

---

## 0. 一分钟版

- **做什么**：按设计方案 v7 实现「产品参数校验系统」。业务流程是：合同号 → 录入卷筒和大车传动参数（可以手动填，也可以语音填）→ 按规则表的 min–max 判定 → 合格的合同导出 Excel。
- **现在的状态**：设计方案 14.2 节里，T1～T7 的功能都已实现，合并到本地 `main`，**450 条测试全部通过**。用真实 FunASR 识别 + 浏览器跑通过一遍完整流程（语音建合同 → 语音录参数 → 不合格 → 复测合格 → 导出 Excel → 下载核对）。
- **还没做**：T8，用真实录音做 A/B 对比测试（需要真人录音）。ClearMic 降噪模块要等队友提供代码。规则现在填的是样例数值，要换成真实值。其他见第 8 节。
- **分工**：后端由 Codex（gpt-6.1-sol，high）编写；Claude 负责审核设计、写前端 `web/`、验收和合并。**这个分工是用户定的，要继续沿用。**

---

## 1. 用户要求（必须遵守）

| 要求 | 出处 |
|---|---|
| 所有给用户看的文字都用中文（包括进度说明、工具调用的描述） | 长期偏好 |
| **按这份设计文档做；不要碰 GitHub 仓库 `kumu314/qc-voice-copilot`，也不用遵守队友的 CONTRIBUTING 规范** | 用户原话：「你别管那个什么队友的那个，你就按照这个文档做，别管github仓库上的东西」 |
| 后端交给 Codex（gpt-6.1-sol，high）实现，Claude 审核设计并验收 | 用户原话 + 设计方案 14.1 节 |
| Codex 不提交代码，由 Claude 审查后提交合并；审查发现的后端问题写成修复说明交给 Codex 修 | 设计方案 14.1 节；此前汴梁项目也是这样约定的 |
| 每个功能做完要跑完整流程：真实后端 + 浏览器 + 真实 FunASR | 设计方案 14.1 节 |
| 只用模拟或脱敏数据，样例数据要标明「样例」 | 赛制要求 |

---

## 2. 位置

| 东西 | 路径 |
|---|---|
| 项目（本地 git，分支 `main`，**没有远程仓库**） | `~/Desktop/产品参数校验系统/` |
| 设计方案 v7 | `~/Desktop/产品参数校验系统/产品参数校验系统设计方案.md` |
| Python 环境 | 项目下的 `.venv`（用 uv 建的，Python 3.12）。已装 fastapi、sqlalchemy 2、openpyxl、pypinyin、httpx、funasr 1.4.24、torch 2.15、modelscope、numpy、scipy、soundfile |
| FunASR 模型缓存 | `~/.cache/modelscope/models/`（paraformer-zh 对应的 SeACo-Paraformer、fsmn-vad、ct-punc，共约 2 GB，已下载） |
| Codex 任务说明和脚本 | `~/worktrees/qc-tasks/`：`common.md`、`B1/T5/T5-fix1/T6/B2.md`、`*.result.md`、`*.log`、`run.sh`、`new_worktree.sh` |
| Codex 工作树 | `~/worktrees/qc/<任务名>`，分支 `codex/<任务名>`。B1、T5、T6、B2 都已合并，可以清理 |
| 语音解析复测集（**故意放在 Codex 读不到的地方**） | `~/worktrees/qc-holdout/parser_holdout.py` |
| 完整流程脚本和合成录音 | `tools/e2e/e2e.cjs`、`tools/e2e/audio/a1～a5.wav` |
| 批量样例数据脚本 | `tools/seed_batch.py` |
| 本次完整流程的截图和导出的 Excel | `端到端截图/`（已加入 .gitignore） |
| ClearMic 队友的技术说明（只有说明，没有代码） | `~/Downloads/reply_to_teammate.md` |
| 之前的「边测边报」静态演示（前端视觉的来源） | `~/Desktop/AI入企黑客松/质检演示/` |

---

## 3. 架构

```
浏览器 web/（单页应用，hash 路由，没用前端框架）
   │  fetch /api/...
   ▼
主程序 app/（FastAPI，端口 8600；同时把 web/ 作为静态文件挂在 / 下）
   ├─ services/contracts   合同号检查（app/htcode.py）、建议合同号、增删改的限制
   ├─ services/records     提交 → 先 commit → 再校验；校验出错时记录保持「待校验」
   ├─ services/validation  judge() 纯函数；补跑校验、重新校验
   ├─ services/rules       规则维护（min ≤ max 检查）
   ├─ services/status      合同状态实时推算
   ├─ services/export      逐个工作表生成，单个工作表出错不影响其他工作表
   ├─ export/sheet_*.py    每个工作表一个生成器，自己查数据库
   ├─ voice/parser.py      文字 → 字段（关键词、拼音多音字容错、纠错表、中文数字、单位、合同号）
   ├─ api/voice.py         把音频转发给识别服务，再调用 parser
   ├─ storage.py           导出文件存储（本地目录；COS 接口已预留但未实现）
   └─ backup.py            启动时检查，当天还没备份就做一次 SQLite 备份，保留 30 天
   │
   ├─ SQLite：数值按整数存，单位是 0.01（app/dec.py 里的 Dec2），避免浮点误差
   ▼
识别服务 asr/（独立进程，端口 8601）
   ffmpeg 转 16k 单声道 → denoise.apply（ClearMic 适配层：可开关，出错自动回退）→ FunASR → 文字
   模型在后台线程加载，约 30 秒；加载期间 /health 返回 loaded=false，/transcribe 返回 503
```

配置都在 `config/`：`fields.json`（校验字段清单）、`sheets.json`（Excel 工作表清单）、`voice.json`（关键词、纠错表、热词、改口词）。识别服务的热词在 `asr/hotwords.txt`。

### 接口一览（都在 `/api` 下）
`GET health` · `GET fields` · `GET contracts/suggest` · `POST/GET contracts` · `GET/PATCH/DELETE contracts/{ht}` · `POST/GET contracts/{ht}/records/{drum|travel_drive}` · `POST validation/pending` · `POST contracts/{ht}/revalidate` · `GET rules` · `PUT rules/{id}` · `POST contracts/{ht}/export` · `POST export/all` · `GET exports` · `GET exports/{ht}/{yyyymmdd}` · `GET voice/status` · `POST voice/parse-text` · `POST voice/parse`

所有错误都返回 `{"error": {"code", "message", ...}}`。数据库不可用时返回 503 `DB_UNAVAILABLE`。

---

## 4. 怎么运行

```bash
cd ~/Desktop/产品参数校验系统

# 测试（450 条，约 3 秒；不加载 FunASR 模型）
.venv/bin/python -m pytest -q

# 主程序（默认数据库 data/qc.sqlite3；用环境变量 QC_DB_PATH / QC_EXPORT_DIR 可以另指）
.venv/bin/uvicorn app.main:app --port 8600

# 识别服务（真实 FunASR；ASR_ENGINE=fake 时用假引擎；ASR_DENOISE=0 关掉降噪）
.venv/bin/uvicorn asr.server:app --host 127.0.0.1 --port 8601

# 样例数据
.venv/bin/python -m app.seed_demo          # 规则全未配置时写入样例规则 + 3 个样例合同
.venv/bin/python tools/seed_batch.py       # 再通过接口录 18 个样例合同（需要规则已配置）

# 完整流程（主程序和识别服务都要先启动；用本机 Chrome + 假麦克风播放 wav）
node tools/e2e/e2e.cjs tools/e2e           # 截图和下载的 xlsx 都输出到 tools/e2e/
```

**交接时正在运行的进程**（关掉也没关系）：
- 主程序 8600 端口，用的是 `data/e2e.sqlite3`，里面有 19 个合同：完整流程测试建的 1 个 + 批量造的 18 个。导出目录是 `exports-e2e/`。
- 识别服务 8601 端口。
- 另外还有一个样例库 `data/demo.sqlite3`（seed_demo 生成的 3 个合同）。

**环境上的坑：**
- Playwright（在 `~/Desktop/代码解释器/node_modules/playwright`）自带的 chromium 已经没了，脚本里要用 `chromium.launch({channel:'chrome'})`，调用本机的 Chrome。
- 假麦克风用的启动参数：`--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<wav>%noloop`。每段录音要单独启动一次浏览器。
- 本机 8765 端口被一个和本项目无关的 Python 服务占着，别去动它。
- 浏览器只在 localhost 或 https 下才允许用麦克风。

---

## 5. Codex 流水线

```bash
T=~/worktrees/qc-tasks
zsh $T/new_worktree.sh <任务>           # 从 main 建分支 codex/<任务>，并把 .venv 链接过去
zsh $T/run.sh <任务> [工作树名]          # 运行时拼接 common.md + <任务>.md；结果写到 <任务>.result.md，日志写到 <任务>.log
```

- **启动后先确认模型**：看 `<任务>.log` 开头是不是 `model: gpt-6.1-sol`、`provider: magpie_http`。
- **在后台运行**（Bash 的 run_in_background），跑完会自动通知。
- **说明要写死**：字段、文件、函数名、提示文案、测试用例都定下来，并写明「不要比较设计方案」。过去 Codex 遇到开放式说明，会花很长时间比较方案却不动手。
- **验收步骤**：
  1. 复跑测试；
  2. 用 grep 检查代码里有没有针对测试句子写的特判；
  3. 用复测集跑一遍语音解析；
  4. 读关键代码；
  5. 用 `user.name=local` 提交（本机没有设全局 git 身份），提交信息末尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`；
  6. 回到 main 执行 `git merge --no-edit codex/<任务>`，再跑一遍全部测试。
- **Codex 额度用完时**：等额度恢复后重跑同一份说明，不要自己接手写后端。
- **前端 `web/` 归 Claude 负责**，Codex 的说明里都写了「不要动 web/」。

| 任务 | 内容 | 结果 |
|---|---|---|
| B1 | 设计方案 T1+T2+T3：骨架、合同、录入、校验、状态推算、接口、seed_demo | 118 条测试，一次通过 |
| T5 | 语音文字 → 字段 | 199 条测试通过，但复测 33/36 → 返工（T5-fix1）→ 254 条测试，复测 36/36 |
| T6 | 识别服务 | 30 条测试，一次通过 |
| B2 | Excel 导出、语音接口、备份，并顺带修了前几个任务的三处问题 | 合并后共 450 条测试 |

---

## 6. 审核时补充的设计决定（设计方案原文里没有，或者有歧义）

这些已经写进代码和测试。如果要改，先跟用户确认。

1. **数值存储**：SQLite 里按整数存，单位是 0.01（Dec2）。比较一律用 Decimal 精确小数。
2. **手动输入超过两位小数**：拒绝，不四舍五入。但多出来的位全是 0 时（如 12.500）会接受。语音里说米、厘米经换算后，四舍五入到两位，并在表单上提示用户核对。
3. **合同状态的优先级**：未录全 > 不合格 > 待校验 > 合格。原文 4.4 节没说「不合格」和「待校验」同时成立时算哪个，我们定为「不合格」优先。
4. **「重新校验」的范围**：按当前规则重新判定该合同的**全部历史记录**，不只是最新一条。
5. **关键词防误命中**：
   - 「长」「厚」这类单字关键词只做精确匹配，而且后面必须紧跟数字才算命中；
   - 拼音容错只对两个字以上的关键词做，按逐字的读音集合求交集，所以支持多音字；
   - 不做编辑距离匹配，也不做前后鼻音、平翘舌这类模糊音；
   - 用短关键词（减速机、电机）填铭牌时，内容里必须有字母或数字。
6. **数值必须紧跟关键词**：关键词后面只允许「是 / 为 / 约 / 大概」这类填充词。例如「长度等一下再测」不会把「一」填进长度。
7. **口语和模糊说法**：「两米九九八」按 2.998 米处理；含「几 / 多 / 来」的模糊说法不填。
8. **改口**：同一字段后说的覆盖先说的。「不对 / 说错了 / 更正」后面只说数字、没说关键词时，这个数字赋给上一个填过的字段。
9. **日期**：语音不填日期，由表单本身默认今天。这样处理是为了解决原文 6.7 节和 6.8 节之间的矛盾。
10. **技术栈调整**：页面不用 Jinja2 模板，改成 JSON 接口 + 静态前端；不用 Alembic，改为启动时 `create_all` 并补齐缺失的规则（幂等）。
11. **ClearMic 接入约定**：放到 `asr/vendor/clearmic/asr_frontend.py`，提供 `process(in_wav, out_wav, vad=False) -> {"action","snr_db","applied"}`。文件不存在时自动直通。调用时 vad 固定传 False，端点检测只交给 FunASR 的 fsmn-vad 做。

---

## 7. 质量证据

- **测试**：`pytest` 共 450 条，覆盖设计方案 13.1～13.6 节：校验边界、SQLite 精度、合同号、流程、导出隔离、语音解析、降噪回退、后台加载。
- **语音解析复测集**：`~/worktrees/qc-holdout/parser_holdout.py`，当前 36/36 通过。还临时测过另外 12 句，过了 11 句，失败的「一点几米」已在 B2 里修好。**以后改 parser 之后都要跑一遍，而且要另外再准备一批新说法测**，防止只是针对已知用例打补丁。
- **完整流程**（2026-11-10 13:21）：真实 FunASR，每段约 4 秒的合成录音识别耗时 0.65～0.83 秒，5 段录音的识别文字全部正确。导出的 Excel 逐个单元格核对过：取的是复测后的最新一条；合同号是文本格式（`@`）；日期是日期格式；数值格式为 `0.00`。
- **前端截图**：见 `端到端截图/1～8`。其中 2、3 两张截图里，麦克风下面还留着一条红色虚线，那是截图之后才修掉的显示问题（录音结束后波形没隐藏）。

---

## 8. 待办（按优先级）

1. **T8：用真实录音做 A/B 对比测试**（设计方案 6.9 节、13.6 节、13.7 节）。需要用户安排真人录音：安静环境和车间环境各一批，每个字段 3～5 种说法，另外留一批只用于最终检验、不参与调试。指标是字段回填准确率。还要对比加热词和不加热词的效果。**目前的合成语音结果不能当作识别率对外宣传。**
2. **ClearMic 接入**：队友交付 `asr_frontend.py` 后放到上面第 6 节第 11 条说的位置，然后跑设计方案 13.6 节的用例，以及真实录音的 A/B 对比。
3. **规则真实值**：现在的 5 条规则（1240～1260 等）是样例数值。
4. **已定（用户 2026-11-10）**：规则保持**全局一套**，不做按合同/图纸覆盖；噪声指生产过程中的机器运行噪音，保持按分贝数值 + 全局上下限校验。真手机实测（T8）暂缓，待定。
5. **还没做的小项**：
   - 腾讯云部署（Docker Compose、Nginx、HTTPS、ICP 备案）；
   - COS 存储（`QC_STORAGE=cos` 目前会抛出 NotImplementedError）；
   - 设计方案 13.8 节的迁移测试（把数据库复制到另一台机器，校验结论和导出结果要一致）。
6. **清理**：B1、T5、T6、B2 的工作树和 `codex/*` 分支都已合并，可以执行 `git worktree remove`（不要加 --force）和 `git branch -d` 删除。删除前先确认工作树里没有未提交的改动。

---

## 9. 和其他项目的关系（避免混淆）

- **`~/Desktop/AI入企黑客松/质检演示/`**：卫华黑客松 #13「边测边报」的纯静态演示（大屏驾驶舱 + 手机端，检验项和公差都是编的）。本项目的前端视觉沿用自它：深色背景、荧光绿强调色、大号细体数字。两者代码不共享。
- **GitHub `kumu314/qc-voice-copilot`**：之前向它推过一个 PR #1（静态演示），这个 PR 还开着。用户已经说了不用再管它，**不要往那个仓库推本项目的代码**。
