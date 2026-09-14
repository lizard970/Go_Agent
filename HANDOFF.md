# 真实 KataGo 接入（2026-09-14，当前阶段）

## 方向与范围

最终方向：图片/SGF → 共享 Position → KataGo → 结构化 Evidence → LLM/Agent →
Memory/RAG。专业数值由 KataGo 计算，后续结构化 DB 保存精确证据。本阶段仅完成
SGF 单局面真实引擎闭环，不实现 persistent process、全盘快扫、关键手检测、
Agent、LLM 联动、Memory schema、retry/fallback 或 metrics 系统。

## 当前实现与调用关系

`app.py` → `sgf_ingestion.parse_sgf().position(n)` → `board_state.Position`
→ `LocalKataGoAdapter.analyze(position, max_visits=None)` → 真实 `katago.exe analysis`
→ `normalize_output()` → `AnalysisResult` / `Candidate` → Streamlit。

- 复用 `KataGoAdapter` Protocol；仍为单次启动进程。以后 persistent adapter 保持同一接口，
  不需要改 SGF ingestion 或让 UI 拼 JSON。无额外 service 层。
- `Position` / `Move` 移至共享棋盘模块，SGF 模块保留导入兼容；图片数据流尚未改为 Position。
- 标准结果保留 `current_player`、`move_number`、`winrate`、`score_lead`、`visits`、
  `best_move`、候选着及其 visits / probability / PV、最佳 PV、policy、warnings 和 status/error。
  所有胜率、目差均为黑棋视角，通过进程级 `-override-config reportAnalysisWinratesAs=BLACK`
  保证，不依赖用户配置的默认视角。手番与评估视角分别表达。
- 真机发现终局候选 pass 的 PV 可以为空；忠实保留 `[]`，不伪造变化。
- UI 只消费标准对象。结果保存在当前 session，改变手数/棋谱时清除；失败明确展示，
  不调用 GPT 兜底。图片入口增加明确标题。
- OpenAI 客户端延迟到调用时创建，SGF 页面不再要求 OpenAI key；GPT 模型、提示词、
  请求参数、错题本和 embedding 逻辑保持不变。未调用真实 GPT。

## 本机配置与启动

`.env` 仅更新 KataGo 的五个设置，其他原有行已逐行验证未改动，不输出或提交 key。
`.env.example` 给出本机路径；进程环境优先于项目 `.env`。

- executable: `D:/gpt/workspace/GoGame/katago/katago.exe`（v1.18.2 CUDA）
- config: `D:/gpt/workspace/GoGame/katago/analysis_example.cfg`
- model: `katago/models/kata1-tf3-b10c512-s3203M-d5937M.bin.gz`
- timeout: 120 秒
- `KATAGO_DLL_DIRECTORY`: `D:/NVIDIA/cuDNN/9.8.0.87/cudnn-windows-x86_64-9.8.0.87_cuda12-archive/bin`
- 本地 analysis config 调整为 `numAnalysisThreads=1`、`numSearchThreadsPerAnalysisThread=5`，
  保留 `maxVisits=500`。该 config 随本地安装目录忽略；重新安装需应用这两项线程设置。

双击 `start.bat` 即可，沿用已修复的项目相对工作目录，直接使用 `venv/Scripts/python.exe`。
脚本通过 `setlocal` 设置 cuDNN PATH，不改 Windows 全局 PATH、不复制依赖。
Adapter 也为自己的子进程设置运行库路径，因此直接 Python 集成调用不依赖先运行 bat。
工作目录设为引擎目录，运行日志保留在忽略的 `katago/` 下。
当前项目 venv 已恢复：Python 3.12，Streamlit 1.63.0。

开发依赖：`venv\Scripts\python.exe -m pip install -r requirements-dev.txt`。
生产应用不依赖 pytest/Playwright。

## 可复用验证命令

```powershell
# 普通测试：不会启动 KataGo，不需要 GPU，不调用 LLM
.\venv\Scripts\python.exe -m pytest -q

# 可选真机单局面测试；配置错误时失败，不静默跳过
$env:RUN_KATAGO_INTEGRATION='1'
$env:KATAGO_TEST_ARTIFACT_DIR='temp\integration'
.\venv\Scripts\python.exe -m pytest -q -s -m integration
Remove-Item Env:RUN_KATAGO_INTEGRATION

# 另一个终端启动应用；可使用自己的空闲端口
.\start.bat --server.port=8503 --server.headless=true --server.address=127.0.0.1
# 浏览器端到端，使用本机 Edge；没装 Edge 可设 chrome 或安装 Playwright Chromium
$env:GO_REVIEW_E2E_URL='http://127.0.0.1:8503'
$env:GO_REVIEW_BROWSER_CHANNEL='msedge'
.\venv\Scripts\python.exe -m pytest -q -s -m e2e
Remove-Item Env:GO_REVIEW_E2E_URL
```

固定验收棋谱：`(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])`。
move 2 为黑 B9、白 A9；move 3 提走白 A9；move 4 白 pass，下一手黑。
引擎结果不是固定断言，实际搜索会小幅波动；配置 500 visits 实际可能略超出（本机 504）。

## 验证记录

以下记录来自真实 CUDA 引擎运行；不作为运行时返回值或 fake fallback。

- Python move 2: winrate=0.976134785, score_lead=3.46206967, best=F5, visits=504, PV=F5 → C4 → F3 → G7 → C6 → E4。
- Python move 4: winrate=0.998086028, score_lead=9.38724122, best=F5, visits=504, PV=F5 → D3 → F3 → C5 → D6 → D5。

浏览器通过真实上传控件、手数选择、分析按钮调用实际引擎，页面展示结果：

- move 2: 黑棋胜率：97.6%；黑棋领先：3.47212 目；最佳着：F5；当前手番：黑棋；搜索次数（visits）：504；最佳变化（PV）：F5 → C4 → F3 → G7 → C6 → E4。
- move 4: 黑棋胜率：99.8%；黑棋领先：9.36862 目；最佳着：F5；当前手番：黑棋；搜索次数（visits）：504；最佳变化（PV）：F5 → D3 → F3 → C5 → D6 → D5。

标准对象 JSON、浏览器文本与截图保存在本地 `temp/integration/`（忽略，不提交）。
浏览器还验证更换 SGF 清除旧结果，以及图片上传入口可见。
图片识别 → 手动确认 → GPT 点评的 AppTest 离线回归通过；未验证真实 OpenAI 服务。

测试结果：普通 pytest **56 passed, 3 skipped**（2 个真机测试、1 个浏览器测试默认关闭）；
真机 integration **2 passed**；真实 Edge/Streamlit/KataGo E2E **1 passed**。
Python compileall、`git diff --check`、`git diff --cached --check` 均通过。

## 遗留与下一阶段

- 当前仍逐次启动引擎，存在模型加载开销；不含全盘分析或错误事件持久化。
- 继承现有 SGF 范围：首变化主线、2–19 路、拒绝中途 setup/PL 编辑；重建不自行裁定 ko，
  交由保留历史的引擎请求按规则处理。未扩展规则裁判功能。
- 默认 RU/KM 缺失仍采用 Japanese/6.5 并在 UI 显示。
- 下一阶段自然切入点：实现同接口的 `PersistentKataGoAdapter`（进程生命周期、query id 与
  响应匹配），复用本阶段的协议/真机测试；之后再做全盘快扫，不提前耦合 LLM/Memory。
- 仅本地 commit，不 push。保留用户原有 `.gitignore` 的 `test_cases/` 修改；不提交本地模型、
  引擎、数据库、凭据。启动脚本在用户修复基础上继续改动。

协议依据：[KataGo v1.18.2 Analysis Engine](https://github.com/lightvector/KataGo/blob/v1.18.2/docs/Analysis_Engine.md)。

---

以下为上一阶段记录（其“未配置/未验证”状态已被上述真机验证取代）：

# SGF ingestion + KataGo slice

Implemented in D:/gpt/workspace/GoGame, the existing Streamlit project. The
original task workspace was AestheticLens and was left unchanged. GoGame had no
Git metadata; initialized codex/sgf-katago locally. The first commit therefore
also tracks existing application source. Credentials, database, images and venv
are excluded. No push.

SGF upload selects first-variation mainline positions, defaulting to final move.
Move 0 is setup. Coordinates reuse extracted board conversions; sgfmill handles
captures. Supports root AB/AW/AE, handicap/PL, pass, metadata and SGF encodings.
Rejects collections, non-Go games, boards outside 2–19, conflicting setup,
occupied-point moves and midgame setup/PL edits. Reconstruction is not a full
rules adjudicator: sgfmill allows suicide and does not enforce ko; preserved
history is sent to KataGo for rules validation. Unknown rules yield engine errors
or visible engine warnings. Missing RU/KM defaults to Japanese/6.5, shown in UI.

KataGoAdapter is a protocol; LocalKataGoAdapter accepts explicit paths or env.
Position and AnalysisResult are independent of Streamlit. Snapshot callers supply
next_player, rules and komi; known SGF histories are preserved. Evaluations use
Black's perspective; candidates are sorted by engine order. Raw policy uses
KataGo's board-index order plus pass, with -1 for illegal moves. Subprocesses have
a timeout and are reaped by subprocess.run. No fake analysis or GPT fallback.
GPT prompts, notebook and embedding logic remain unchanged.

## Configuration and verification

Use a working Python (the pre-existing venv references a missing D:/anaconda).
`python -m pip install -r requirements.txt`; install pytest for tests.
Set KATAGO_EXECUTABLE, KATAGO_CONFIG (analysis config), KATAGO_MODEL, and optional
KATAGO_TIMEOUT (seconds, default 120) in the existing .env or process environment.
Paths with spaces are supported. Preserve the existing OpenAI configuration for
screenshot/commentary features. Launch with `python -m streamlit run app.py`.

Upload `(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])`. Check move 0 empty, move 2 has
black B9/white A9, move 3 captures white A9, and final move 4 passes unchanged.
Click KataGo analysis with paths unset: explicit unavailable error, no GPT call.
Configure real engine paths; analyze moves 2 and 4, check Black-perspective
winrate/lead, candidates and warnings. Confirm screenshot/manual editing, GPT
commentary and the existing notebook workflow still operate with configured
provider credentials.

## Validation

`python -m pytest -q`: 30 passed (offline protocol responses are test fixtures only).
`python -m compileall -q app.py analyzer.py memory_store.py board_state.py sgf_ingestion.py katago_adapter.py`: passed.
`git diff --cached --check`: passed.
UI smoke test could not run: Streamlit is absent in working Python and its installation failed with a proxy connection reset. Real KataGo/GPU/model and live GPT calls remain unverified; no engine is configured.

Exact next step: configure real KataGo analysis executable/config/model paths,
then run the SGF move-2/move-4 smoke test above before integrating engine output
with GPT commentary.

Protocol reference: https://github.com/lightvector/KataGo/blob/master/docs/Analysis_Engine.md
