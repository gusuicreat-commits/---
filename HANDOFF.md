# 交接文档 · 产品参数校验系统（边测边报）

> 更新于 2026-11-10 14:40（北京时间），写给下一个接手的 agent。
> 阅读顺序：本文件 → `产品参数校验系统设计方案.md`（v7，需求唯一来源）→ 需要时看 `~/worktrees/qc-tasks/B*.md`（每个后端任务的定稿说明）。

---

## 0. 一分钟版

- **是什么**：卫华 AI 入企黑客松 13 号选题「边测边报」的真系统版。质检员在手机上按住说「参数 + 数值」→ 本机 FunASR 识别 → 三层纠错 → 解析成字段 → 按全局 min–max 规则当场判定 → 提交入库；两类参数（卷筒、大车传动）最新记录都合格的合同才能导出 Excel。主管在大屏驾驶舱实时看到每一句话的识别、纠错、判定。
- **现在的状态**：设计方案 T1～T7 全部实现；在此基础上按用户要求把前端改成「启动页 + 主管驾驶舱 + 质检员手机端」两端联动，并加了录音证据追溯、听不懂转复核并学习纠错表、一键演示。全部合并在本地 `main`，**546 条 pytest 全过，语音解析留出集 36/36**，两套浏览器全流程脚本（真实 FunASR + 假麦克风）都跑通、无页面报错。
- **分工（用户定的，必须沿用）**：后端由 Codex（gpt-6.1-sol，reasoning high）写；Claude 写任务说明、审核、写前端 `web/`、验收合并。Codex 不提交，Claude 审完再提交合并。
- **还没做**：见第 9 节。最主要的是真手机/真录音实测（用户说暂缓）、ClearMic 降噪代码未到、规则真实值、整改单/飞书、防造假与跨项一致性。

---

## 1. 用户要求（必须遵守）

| 要求 | 来源 |
|---|---|
| 所有给用户看的文字都用中文（包括进度说明、工具调用描述） | 长期偏好 |
| **只按设计文档做；不碰 GitHub 仓库 `kumu314/qc-voice-copilot`（PR #1 还开着，别管），不遵守队友 CONTRIBUTING** | 用户原话：「你别管那个什么队友的那个，你就按照这个文档做，别管github仓库上的东西」 |
| 后端交给 Codex（gpt-6.1-sol high），Claude 审核验收；审核发现的后端问题写成说明交回 Codex | 用户原话 |
| 页面风格主要参考上一版静态演示 `~/Desktop/AI入企黑客松/质检演示/` | 用户原话 |
| 每个功能完成后跑完整流程：真实后端 + 浏览器 + 真实 FunASR，截图给用户看 | 设计方案 14.1、用户要求 |
| 只用模拟或脱敏数据，样例数据标「样例」 | 赛制 |
| 不要动本机 8765 端口上的 Python 服务（与本项目无关） | 环境 |

### 用户已拍板的决定（2026-11-10）
- **规则全局一套**，不做按合同 / 图纸覆盖。
- **噪声 = 生产过程中的机器运行噪音**，保持按 dB 数值 + 全局上下限校验（当前 0～85）。
- **真手机实测（T8）暂缓**，待定。

---

## 2. 位置

| 东西 | 路径 |
|---|---|
| 项目（本地 git，分支 `main`，**无远程**） | `~/Desktop/产品参数校验系统/` |
| 设计方案 v7 | 项目根 `产品参数校验系统设计方案.md` |
| Python 环境 | 项目下 `.venv`（uv，Python 3.12；fastapi、sqlalchemy 2、openpyxl、pypinyin、httpx、funasr 1.4.24、torch、modelscope…） |
| FunASR 模型缓存 | `~/.cache/modelscope/models/`（SeACo-Paraformer、fsmn-vad、ct-punc，约 2 GB，已下载） |
| Codex 任务说明、日志、脚本 | `~/worktrees/qc-tasks/`：`common.md`、`B1/T5/T5-fix1/T6/B2/B3/B4/B5/B6.md`、`*.result.md`、`*.log`、`run.sh`、`new_worktree.sh` |
| Codex 工作树 | `~/worktrees/qc/<任务>`（分支 `codex/<任务>`）——B1～B6、T5、T6 **全部已合并**，可清理 |
| 语音解析留出集（**故意放在 Codex 读不到的地方**） | `~/worktrees/qc-holdout/parser_holdout.py`；运行：`PYTHONPATH=~/Desktop/产品参数校验系统 .venv/bin/python ~/worktrees/qc-holdout/parser_holdout.py` |
| 浏览器全流程脚本 | `tools/e2e/e2e.cjs`（管理后台流程）、`tools/e2e/linked.cjs`（两端联动）、`tools/e2e/demo.cjs`（演示+复核+证据）；合成录音 `tools/e2e/audio/a1～a5.wav` |
| 批量样例数据 | `tools/seed_batch.py`（18 个样例合同） |
| 截图 | `端到端截图/`（1～8 管理后台）、`端到端截图/联动/`（l0～l10）、`端到端截图/演示/`（d2～d16）；已 gitignore |
| 上一版静态演示（页面风格来源） | `~/Desktop/AI入企黑客松/质检演示/`（index / screen / phone / engine.js） |
| ClearMic 队友说明（只有说明，无代码） | `~/Downloads/reply_to_teammate.md` |

---

## 3. 架构

```
手机浏览器 phone.html ─┐                      ┌─ 大屏浏览器 screen.html
                       │  fetch /api/...       │  （EventSource 订阅 /api/events/stream）
                       ▼                       ▼
主程序 app/（FastAPI，8600；同时把 web/ 挂在 / 下）
   ├─ events.py            进程内事件总线（环形缓冲 1000 条 + 订阅队列；publish 永不抛异常）
   ├─ api/events.py        GET /events、SSE /events/stream、POST /events(focus|draft)、/presence
   ├─ services/contracts   合同号检查(htcode.py)、建议合同号、增删改限制
   ├─ services/records     提交 → commit → 校验；校验出错记录保持「待校验」
   ├─ services/validation  judge() 纯函数；补跑、重新校验
   ├─ services/rules       规则维护（min ≤ max）
   ├─ services/status      合同状态实时推算：未录全 > 不合格 > 待校验 > 合格
   ├─ services/export      逐工作表生成，单表出错不影响其他
   ├─ services/voicelog    每句语音写 voice_log、存录音、复核、学习纠错
   ├─ voice/parser.py      文字 → 字段（关键词、拼音多音字容错、纠错表∪学到的纠错、中文数字、单位、合同号）
   ├─ api/voice.py         音频转发识别服务 → parser → 写 voice_log → 发 voice 事件
   ├─ api/evidence.py      语音记录/录音/时间线/复核/纠错表接口
   ├─ demo.py + api/demo.py  一键演示执行器（后台线程按 config/demo.json 调真实服务函数）
   ├─ storage.py           导出文件存储（本地；COS 预留未实现）
   └─ backup.py            每天首次启动备份 SQLite，保留 30 天
   │  SQLite：数值按整数存（单位 0.01，app/dec.py 的 Dec2），比较用 Decimal
   ▼
识别服务 asr/（独立进程，8601）
   ffmpeg 转 16k 单声道 → denoise（ClearMic 适配层，文件不在就直通，出错回退原音）→ FunASR → 文字
   模型后台线程加载约 30 秒；加载中 /health loaded=false，/transcribe 503
```

配置在 `config/`：`fields.json`（字段）、`sheets.json`（Excel 工作表）、`voice.json`（关键词、内置纠错表、热词、改口词）、`demo.json`（演示剧本）。识别热词另在 `asr/hotwords.txt`。

### 数据表
`contract`、`drum_param`、`travel_drive_param`、`rule`（及校验相关）、`voice_log`（每句话：原话、纠错后、解析结果 JSON、录音相对路径、状态 ok/review/resolved/ignored、关联记录）、`learned_correction`（复核学到的纠错，wrong 唯一）。启动时 `create_all`，无 Alembic。

### 接口一览（都在 `/api` 下；错误统一 `{"error":{"code","message",...}}`）
- 基础：`health` · `fields` · `contracts/suggest` · `contracts`(GET/POST) · `contracts/{ht}`(GET/PATCH/DELETE) · `contracts/{ht}/records/{drum|travel_drive}`(GET/POST，POST 可带 `?source=phone|screen&voice_ids=1,2`) · `validation/pending` · `contracts/{ht}/revalidate` · `rules` · `rules/{id}` · `contracts/{ht}/export` · `export/all` · `exports` · `exports/{ht}/{yyyymmdd}`
- 语音：`voice/status` · `voice/config`（含 learned）· `voice/parse-text`（JSON：text、form、ht_code?、source?）· `voice/parse`（multipart：audio、form、ht_code?、source?）——两者返回体含 `voice_id`
- 联动：`events?after=&limit=` · `events/stream?after=`（SSE，先发 `event: hello`，15 秒 ping）· `POST events`（仅 focus/draft）· `presence`(GET/POST，10 秒内上报算在线)
- 证据/复核：`voice/logs` · `voice/logs/{id}` · `voice/logs/{id}/audio` · `contracts/{ht}/timeline` · `review?status=` · `review/{id}/resolve`（learn 需 wrong/right，wrong 必须出现在原话里；ignore）· `corrections`(GET/POST) · `corrections/{id}`(DELETE)
- 演示：`demo/start {speed}` · `demo/stop` · `demo/status` · `demo/reset`

### 事件类型（data 结构见 `~/worktrees/qc-tasks/B3.md`、`B5.md`）
`voice` · `record` · `contract` · `rules` · `export` · `focus` · `draft` · `review` · `corrections` · `demo`；source ∈ `phone|screen|demo|null`。

---

## 4. 前端 `web/`（Claude 负责）

| 文件 | 内容 |
|---|---|
| `index.html` | 启动页（照搬上一版样式）：三张卡片（驾驶舱 / 手机端 / 管理后台）、「同时打开大屏和手机端」、服务状态与合同统计 |
| `common.js` | 两端共用：`QC.api`、字段/规则加载、`prejudge` 本地预判、回读文案、事件订阅 `QC.connect/onEvent`、`publish`、`presence`、录音器 `recorder`、`sendAudio/sendText`、TTS |
| `screen.html` | 主管驾驶舱。标签：**实时检验**（左：检验表 7 项；中：5 步管线 + 原话纠错标注 + 结构化条 + 大字判定 + 回读；右：待复核卡片、不合格/未完成合同可「派给质检员复测」、本场记录、导出闸门）· **识别纠错**（三层纠错配置、本场识别记录、待复核学习、学到的纠错增删）· **证据追溯**（语音记录列表、录音回放、解析过程、合同时间线、进了哪条记录）· **合同台账**（筛选、批量导出、清理演示合同、导出文件）· **验证看板**（KPI、状态分布、不合格参数排行、当前规则）· 底栏（大屏代录输入、提交手机草稿、一键演示/速度、回读播报开关） |
| `phone.html` | 质检员手机端（上一版手机外框、灵动岛提醒、大字、回读、底部麦克风）：选择/新建合同（合同号可按住说）、卷筒/大车传动切换、按住说话（电脑上按住空格）、草稿表、✓ 提交（带 voice_ids）、记录列表；跟随大屏派单和演示 |
| `admin.html` + `app.js` + `app.css` | 原管理后台 SPA（合同增删改、手动录入、历史、重新校验、规则、导出） |

要点：手机草稿存在 localStorage（`qcp_*`），大屏偏好 `qcs_*`；客户端 id 存 `qc_client`。手机浏览器**只在 https（或 localhost）下给麦克风**，真机要用 `tools/run_https.sh`。

---

## 5. 怎么运行

```bash
cd ~/Desktop/产品参数校验系统

# 后端测试（546 条，约 5 秒，不加载 FunASR）
.venv/bin/python -m pytest -q

# 主程序（默认 data/qc.sqlite3；可用 QC_DB_PATH / QC_EXPORT_DIR / QC_AUDIO_DIR 另指）
.venv/bin/uvicorn app.main:app --port 8600
# 真手机：自签名证书 + 0.0.0.0:8443，会打印手机地址
zsh tools/run_https.sh

# 识别服务（ASR_ENGINE=fake 用假引擎；ASR_DENOISE=0 关降噪）
.venv/bin/uvicorn asr.server:app --host 127.0.0.1 --port 8601

# 样例数据
.venv/bin/python -m app.seed_demo          # 规则全未配置时写样例规则 + 3 个样例合同
.venv/bin/python tools/seed_batch.py       # 通过接口录 18 个样例合同

# 浏览器全流程（两个服务都要先起；本机 Chrome + 假麦克风）
node tools/e2e/linked.cjs 端到端截图/联动
node tools/e2e/demo.cjs   端到端截图/演示
node tools/e2e/e2e.cjs    tools/e2e        # 管理后台流程（入口 admin.html）
```

**交接时在跑的进程**（关掉无妨）：
- 主程序 8600：`QC_DB_PATH=data/e2e.sqlite3 QC_EXPORT_DIR=$PWD/exports-e2e QC_AUDIO_DIR=$PWD/data/audio-e2e`，库里 24 个合同（合格 14 / 不合格 7 / 未录全 3），日志 `logs/main-8600.log`。
- 识别服务 8601（真实 FunASR，已加载）。
- 另有样例库 `data/demo.sqlite3`。

**环境的坑**
- Playwright 在 `~/Desktop/代码解释器/node_modules/playwright`（脚本里用绝对路径 require），自带 chromium 没了，必须 `chromium.launch({channel:'chrome'})`。
- 假麦克风：`--use-fake-ui-for-media-stream --use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<wav>%noloop`，一个浏览器进程只能放一段 wav。
- 合同号 `YYYYMMDD-NN`，NN 只能 10～90，**一天只有 9 个号**。`data/e2e.sqlite3` 今天（11-10）已用 10/40/50/60/70/80/90；全流程脚本每跑一次 linked.cjs 就消耗一个号。演示合同号用完会自动回收（见第 7 节）。
- 本机没设 git 全局身份，提交用 `git -c user.name=local -c user.email=local@localhost commit ...`（或沿用当前仓库配置），提交信息末尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

---

## 6. Codex 流水线

```bash
T=~/worktrees/qc-tasks
zsh $T/new_worktree.sh <任务>      # 从 main 建 codex/<任务> 分支和工作树，链接 .venv
zsh $T/run.sh <任务>               # 拼接 common.md + <任务>.md 喂给 codex exec；结果 <任务>.result.md，日志 <任务>.log
```
- 用 Bash 的 `run_in_background` 跑，完成会自动通知；启动后看 log 开头确认 `model: gpt-6.1-sol`。
- **说明要写死**：文件清单、函数签名、接口、字段、文案、测试用例都定下来，并写「不要比较设计方案」。开放式说明会让 Codex 空转。
- Codex 常见情况：遇到「旧测试断言与新需求冲突」且该测试文件不在允许清单时，它会停下不改、在结果里请求确认。**这时审核方确认是旧断言过时后自己改断言即可**（B4、B5 都发生过）。
- **验收步骤**：① 工作树里复跑 pytest；② 读关键代码（尤其安全边界：路径校验、删除条件）；③ 改 parser 的任务必须跑留出集；④ 提交（Codex 不提交）；⑤ 回 main `git merge --no-ff codex/<任务>`，再跑全量；⑥ 重启主程序跑浏览器全流程。
- Codex 额度用完：等恢复后重跑同一份说明，不要自己接手写后端。

| 任务 | 内容 | 结果 |
|---|---|---|
| B1 | T1+T2+T3：骨架、合同、录入、校验、状态、接口、seed_demo | 118 测试 |
| T5 / T5-fix1 | 语音文字 → 字段；留出集 33/36 返工 | 254 测试，留出 36/36 |
| T6 | 识别服务 | 30 测试 |
| B2 | Excel 导出、语音接口、备份、几处修复 | 共 450 |
| B3 | 事件总线 + SSE、在线状态、voice/config、run_https.sh | 共 486 |
| B4 | 解析保留铭牌/客户名大小写 | 共 502（改了 2 条旧断言 + 留出集 1 条期望） |
| B5 | voice_log + 录音、证据/时间线、复核学习纠错、一键演示 | 共 533（改了 1 条旧断言） |
| B6 | 演示合同回收、demo/reset | 共 546 |

---

## 7. 设计决定（审核时补的；改之前先问用户）

1. 数值按整数存（0.01 单位，Dec2），比较一律 Decimal。
2. 手动输入超过两位小数拒绝（末尾全 0 如 12.500 接受）；语音里米/厘米换算后四舍五入两位并提示核对。
3. 合同状态优先级：未录全 > 不合格 > 待校验 > 合格。
4. 「重新校验」重判该合同**全部历史记录**。
5. 关键词防误命中：单字关键词（长、厚）只精确匹配且必须紧跟数字；拼音容错只对 ≥2 字关键词，按逐字读音集合求交（支持多音字）；不做编辑距离和模糊音；短关键词填铭牌时内容须含字母或数字。
6. 数值必须紧跟关键词（只允许「是/为/约/大概」等填充词）；「长度等一下再测」不填。
7. 「两米九九八」= 2.998 米；含「几/多/来」的模糊说法不填。
8. 改口：同字段后说覆盖先说；「不对/说错了/更正」后只说数字时赋给上一个字段。
9. 语音不填日期，表单默认今天。
10. 页面不用 Jinja2，JSON 接口 + 静态前端；无 Alembic。
11. ClearMic 接入约定：`asr/vendor/clearmic/asr_frontend.py`，`process(in_wav, out_wav, vad=False) -> {"action","snr_db","applied"}`；文件不在即直通；vad 固定 False，端点检测只交给 fsmn-vad。
12. 解析结果 fields 为空或有拒收 → `voice_log.status=review`（合同表单只在 fields 为空时）。听不懂**不猜**，转主管复核。
13. 复核「学进纠错表」：wrong 必须出现在原话里；学到的纠错优先于 voice.json 内置纠错；parser 不直接读库，由 voicelog 在启动和增删后调 `set_learned_corrections`。
14. **演示合同的判定**：名称等于 `config/demo.json` 的 name（「演示客户（样例）」）且该合同的语音**全部** source=demo。混入任何真人语音即视为真实数据，永不回收/删除。回收不删导出 xlsx（留档）。
15. 规则全局一套；噪声按 dB 数值校验（用户已定）。

---

## 8. 质量证据

- **pytest 546 条**：校验边界、精度、合同号、流程、导出隔离、语音解析、降噪回退、后台加载、事件总线/SSE、presence、voice_log、录音、时间线、复核学习、演示执行与回收。
- **留出集 36/36**。改 parser 后必须跑，并另造一批新说法测，防止针对已知句子打补丁。
- **浏览器全流程（2026-11-10 下午，真实 FunASR，均无页面报错）**：
  - linked.cjs：手机选合同 → 大屏跟随 → 键入直径超上限（两端同时提醒）→ 按住说话（识别 0.8～1.3 秒）→ 补厚度提交合格 → 大车传动噪声超标不合格、导出被锁 → 大屏派单 → 手机自动切换合同 → 大屏代录 → 各标签截图。
  - demo.cjs（连跑两遍）：一键演示 12 步走完、导出 Excel、闸门显示已导出 → 手机换到其他合同真录音 → 「光蛋三百一十」进待复核 → 学「光蛋→光段」→ 再说「光蛋三百零五」自动填光段 305 → 证据追溯录音回放进度条走动、时间线显示记录依据的语音 id。
- **注意**：识别率只来自合成语音（macOS Tingting），**不能当真实识别率对外宣传**。

---

## 9. 待办（按优先级）

1. **真手机 + 真实录音实测（T8）**——用户说暂缓。届时：`tools/run_https.sh` 起服务，手机连同一 Wi-Fi；安静 / 车间各录一批，每字段 3～5 种说法，另留一批只做最终检验；指标是字段回填准确率；对比加不加热词。
2. **ClearMic 降噪**：队友交付 `asr_frontend.py` 后放到第 7 节第 11 条的位置，跑设计方案 13.6 节用例和真实录音 A/B。
3. **规则真实值**：现在 5 条规则（直径 1240～1260 等、噪声 0～85）都是样例值。
4. **上一版演示有、新系统还没有的亮点**（都是后端改动，照旧交给 Codex，前端 Claude 接）：
   - 整改单 + 飞书推送（需要用户提供飞书机器人 webhook；可先做模拟）；
   - 检验报告纸面 + 出报告前拦截（目前只拦截导出 Excel）；
   - 防造假（数据过于整齐、报数过快）、跨项一致性（对角线差等）、位置防错（左/右）、更多判定类型（公差、等于、派生量）——需先把检验项扩到 v7 之外，**要先问用户**；
   - 离线录入、联网后同步。
5. **部署**：腾讯云（Docker Compose、Nginx、HTTPS、备案）、COS 存储（`QC_STORAGE=cos` 目前 NotImplementedError）、设计方案 13.8 迁移测试。
6. **清理**：`~/worktrees/qc/{B1,B2,B3,B4,B5,B6,T5,T6}` 与对应 `codex/*` 分支都已合并。先 `git -C <工作树> status` 确认干净，再 `git worktree remove <路径>`（不加 --force）和 `git branch -d codex/<任务>`。
7. **小问题**：合同 20261110-60 上的铭牌是小写 `zq500`，是 B4 修复前存的旧测试数据，不是 bug；`20261109-30` 上有几条测试遗留的「光蛋」语音记录（样例合同，无提交）。

---

## 10. 和其他项目的关系

- **`~/Desktop/AI入企黑客松/质检演示/`**：#13「边测边报」纯静态演示（剧本驱动，检验项和公差是编的）。本项目前端页面结构和视觉照它做，但代码不共享。两版功能差异见第 9 节第 4 条。
- **GitHub `kumu314/qc-voice-copilot`**：之前推过静态演示的 PR #1，还开着。用户已说不用管，**不要往那个仓库推本项目代码**。
