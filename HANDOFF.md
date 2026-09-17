# 错题回局、局面快照与看板统计修正（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- `memory_games` 兼容迁移新增 nullable `source_content`，每个 game id 只保存一次原始/canonical SGF；重复事件
  upsert 使用 `COALESCE` 保留已有源数据，不为每手复制完整棋盘。
- `memory_error_events` 兼容迁移新增 `board_snapshot_json`。保存 finalized ErrorEvent 时，从 SGF 重建
  worst move 局面并存紧凑 `{board_size, black, white, move_number}` 坐标快照；旧的非 SGF/fake source 仍可保存，
  只是没有快照。
- 事件级错题读取现在保留 `game_id`、worst move、源 SGF 链接及快照。错题本详情直接从快照画历史棋盘，
  不调用 KataGo/OpenAI。
- 错题本新增“回到棋局查看”。点击后从 SQLite 读取源 SGF，优先按 source fingerprint/user color 恢复
  full-review cache，把工作台唯一 current-move key 设置为 worst move，再切换到分析工作台；board、commentary、
  evidence 与 timeline 都显示缓存内容。没有完整 cache 时使用源 SGF + 快照 + 已存 explanation 显示只读历史局面。
- 新增 `load_mistake_game_context()`，集中返回 source、snapshot、primary move、保存证据和最佳可用 review cache。
- 看板不再从 `load_mistake_records()` 计数。旧逻辑把 legacy game row 和每个 ErrorEvent row 混为同一种
  record，直接 `len(records)` 导致一盘多个错题被算成多盘；同时把 explanation headline 填进 `issue_type`，
  再用 Counter 当作规范错误类型，造成“N17 是全局最大失分点”一类自然语言被误报为高频类别。
- 新增 `load_dashboard_summary()`：总复盘数、近 30 天和趋势按 source fingerprint 去重的 completed games
  统计；累计错题数按个人 `memory_error_events` 统计；仅展示结构化 phase 分布。高频错误类型、重复类型占比、
  headline 类型分布已移除，没有新增 LLM 分类调用。

## 验证

- 错题/缓存/Streamlit 定向：**24 passed**。
- 完整非真机套件：`pytest -q` -> **168 passed, 5 skipped**。
- 覆盖 SGF 只存一份、worst-move 快照坐标、game/event identity、回局定位、缓存 commentary 恢复、导航零 API、
  同盘两个 ErrorEvent 仍只算一次复盘、累计错题数与 phase 分布。
- 未调用真实 OpenAI 或 KataGo。

## 兼容边界

- 旧 event row 没有 `source_content` 或 snapshot 时仍可读取；无法回局时 UI 明确提示原始棋谱不可用。
- review cache 仍是同版本本地 Python snapshot；本阶段没有改成跨版本/多用户交换格式。

---

# 最终功能稳定化（2026-09-17，未提交实现，以工作树为准）

## 本阶段修复

- 首页执行加入轻量 `st.status`。SGF 解析、逐局面 KataGo 进度、位置筛选、批量点评、历史检查和完成态
  通过 callback 更新单一状态标签；没有动画 stepper。`scan_game()` 的 callback 是可选参数，既有调用不受影响。
- 分析工作台的当前手改为一个 session-state key，同时也是 slider widget key。上一手/下一手用 widget callback
  修改同一个 key，消除了按钮写入后被旧 slider key 覆盖的问题；board/comment/timeline 都从该 key 读取。
- 播放器棋盘列比例提高到 1.18:0.82，重要度时间线移到棋盘正下方并默认低透明度、hover 时强调；控制区移到
  点评列下方，通过小间隔保持在播放器右下区域。未使用自定义 JS。
- 在既有 SQLite 新增窄表 `review_run_cache`。完成态 `HomeReviewRun` 以内部 BLOB 保存，cache id 包含 SGF
  fingerprint、用户颜色、最终 route、target move 和 goal fingerprint；request lookup 使用同一分析输入。
- 首页启动时恢复最近完成结果；相同输入再次提交直接从 SQLite 恢复，不创建 KataGo adapter，也不调用
  controller/commentary/embedding。缓存保存 whole-game scan、importance、comments、deep evidence、final answer、
  history summary 和保存状态。partial/controller-error 不写缓存。
- “保存到错题本”现在返回 `saved/already_saved/not_saveable/failed` 四态，UI 始终显示对应的保存成功、已经保存、
  当前着不可保存或具体失败消息。事件保存成功后同步刷新 review cache；cache 刷新失败不回滚已提交的错题事务。
- `load_mistake_records()` 已把个人 `memory_error_events` 合并到既有错题本读取路径。事件级记录没有截图时，
  错题本明确显示无棋盘快照，并继续展示手数、解释、实战/推荐着和改进建议。
- 事件时间戳使用 ISO 8601；成长看板改为兼容旧 SQLite 时间格式与带时区 ISO 时间，修复事件错题加入后页面崩溃。
- targeted/memory cache 不会误进入需要 `GameScanResult` 的整盘播放器；仍在首页展示其自身结果。

## 验证

- 稳定化定向测试：**48 passed**。
- 完整非真机套件：`pytest -q` -> **166 passed, 5 skipped**。
- 覆盖逐局面进度、整盘/定点状态阶段、SQLite 完成态恢复且零昂贵调用、cache identity 隔离、首次点击下一手、
  slider/board/comment 同步、纯导航零 API、点评跨 rerun、保存幂等、不可保存反馈及事件错题刷新后可读。
- 未调用真实 OpenAI；未执行可选真实 KataGo smoke。

## 剩余限制

- cache BLOB 是本应用内部 Python 对象快照，适合同版本本地桌面运行；未来若需要跨版本迁移或多用户服务，
  应另做版本化 JSON schema。本阶段没有扩展为新产品架构。
- 事件级错题当前数据库没有单独棋盘快照，因此错题本不会伪造棋盘；完整 SGF 局面仍可从 review cache/工作台查看。
- 手动高级 SGF 面板仍是独立流程；整盘播放器消费已完成的首页 full-review cache。

---

# 逐手点评三项边界修正（2026-09-17，未提交实现，以工作树为准）

- Top-K 与绝对严重度已经解耦。`is_top_error` 只表示该事件在本局的相对排名；
  `quiet/minor/major/critical` 只由 `MoveImportanceConfig` 的独立胜率/目差阈值决定。低损失 Top1
  可以保持 minor，达到 absolute critical 阈值的 Top Error 仍为 critical。
- `MoveImportanceConfig` 新增 major/critical 阈值；Top 标记不再强制提高 level 或把重要度拉到 1.0。
- `MoveCommentaryService` 新增可配置 `max_batch_size`，默认读取
  `MOVE_COMMENTARY_MAX_BATCH_SIZE`（缺省 20）。正常集合仍为一次请求；超限时保持原选择和顺序，按固定
  大小确定性切批，逐批严格校验 move number，最后恢复输入顺序。不会退化成逐手调用。
- selective commentary 现在允许对手的 major、critical 和真正突出正向着进入批量请求；普通对手 minor
  仍只保留本地 metadata。证据新增 `is_user_move`，prompt 强制把对手着写成“对手的选择/用户应对学习点”，
  不得称为用户失误。
- 个人 ErrorEvent Top-K、Memory ownership 和保存校验未改变；对手事件仍不能保存为用户个人错题。
- 定向测试：`pytest -q test_move_commentary.py test_review_agent.py` -> **21 passed**。
- 完整非真机套件：`pytest -q` -> **162 passed, 5 skipped**。

---

# 逐手重要性、选择性批量点评与工作台播放器（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- 新增 `move_commentary.py`。`build_move_importance()` 为整盘每一手生成 0..1 重要度、
  `quiet/minor/major/critical`、正负/中性方向、Top Error 标记和既有 ErrorEvent 引用；不调用 LLM。
- 重要度直接复用现有行棋方胜率/目差损失、ErrorEvent 与用户色 Top-K 排名。Top-K 仅表示本局相对排名，
  绝对 commentary level 由独立阈值决定；LLM 文本长度不会参与排序。
- 已有 `AnalysisResult.candidates/pv` 足够支持正向识别，无需修改 KataGo adapter。只有实战匹配首选且首选
  相对第二候选达到可配置评价差时，才标为值得关注的正向着；不会仅凭“等于首选”称为妙手。
- quiet 不进入 LLM。用户的非 quiet 着及对手 major/critical/突出正向着被转换为 `MoveCommentaryEvidence`，包含紧凑棋局
  上下文、黑棋视角前后评价、行棋方损失、推荐着、Top-3 候选、PV；critical 另含 500 visits deep evidence。
- `MoveCommentaryService.comment_many()` 正常用一次严格 JSON Schema 请求处理选中着；超出安全上限时
  确定性切成少量有界批次，并按 move number 校验、重排结果。用户只看到 headline + 自然中文 commentary；内部语义字段复用为既有 Memory 的
  `ExplanationResult`，因此整盘保持 1 planner + 1 commentary batch + 1 history embedding batch 的预算。
- 定点复盘仍只分析目标手前后两个局面，不做全盘扫描。即使目标手未达到 ErrorEvent 阈值，也构造
  targeted evidence 并生成详细点评，同时保留 `is_top_error=False`，不会强称失误。
- `DeepAnalysisEvidence` 向后兼容地新增 PV、Top-3 candidates 和紧凑 game context；canonical Memory evidence
  schema 和 SQLite schema 未改变。
- 分析工作台发现首页 session 中的整盘 Agent 结果后，显示逐手播放器：棋盘、前后按钮、手数 slider、
  推荐着/评价、缓存点评、Top Error/历史候选提示，以及同步重要度时间线。移动手数只读缓存，不调用 API。
- 时间线覆盖全部手数，以高度表达重要度，以颜色区分正向/负向/中性关注，并用竖线同步当前手；相邻
  ErrorEvent 候选通过连续面积线自然形成区域。未引入自定义 JS 或新前端框架。

## 验证

- 新模块、ReviewAgent、错误检测/排名、deep/explanation、Streamlit 定向测试：**80 passed**。
- 完整非真机套件：`pytest -q` -> **159 passed, 5 skipped**。
- 覆盖每手 metadata、quiet 排除、comment-worthy 纳入、Top Error critical、候选着正向区分、一次批量
  多手、level 入 prompt、move-number 回填、定点跳过 full scan、非 ErrorEvent 定点点评、全局 timeline、
  工作台换手不调用 API、点评跨 rerun 保留，以及 1/1/1 模型调用预算。
- 自动化测试未调用真实 KataGo 或 OpenAI。

## 当前限制 / 下一步

- 候选着与 PV 已从 low/deep normalized result 提供；正向“突出程度”目前只比较首选与第二候选的
  winrate/score gap，仍需真实棋谱校准。没有 ownership map，也没有声称候选差异揭示具体战术原因。
- 工作台播放器消费首页已完成的 Agent session；手动 SGF/KataGo 面板保留为高级入口，尚未合并成同一个
  启动控件。真实浏览器 E2E 应从首页完成整盘复盘，再进入分析工作台逐手播放并验证真实批量响应。
- 尚未实现温和/中性/严厉语气渲染，符合本阶段边界。

---

# ReviewAgent 首页接入（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- 首页现在是 ReviewAgent 主入口：上传 SGF、选择黑/白、输入自然语言目标、可选目标手，点击一次即可运行
  既有 `ReviewAgent` 流程并在原页展示结果。
- 新增 `agent_home.py` 作为窄 UI 边界。它复用 `parse_sgf()`、`PersistentKataGoAdapter`、
  `ErrorExplanationService`、`analyzer.get_embeddings`、`OpenAIReviewController` 和 `ReviewAgentContext`；
  每次显式提交创建 adapter，并在成功或失败后始终 `close()`。
- 首页结果保存在 `st.session_state["agent_home_review_run"]`。普通 widget rerun、展开调试信息或切换页面
  不会再次调用 KataGo/OpenAI；只有再次点击“开始复盘”才执行新请求。
- 正常区域显示 Agent answer、逐条结构化错误解释和 explanation pending 提示。只有 state 中存在有意义历史
  signal 时才显示潜在相似记录；措辞明确相似度不是同类错误的确认。
- controller/explanation/embedding 调用数与轻量 trace 放在“运行信息” expander，不向主界面倾倒 raw JSON。
- 首页通过 UI controller wrapper 强制本轮 plan 的 `save_memory=False`。只有用户点击“保存到错题本”后，
  才复用既有 `save_error_memory` tool 保存已完成且属于用户颜色的事件；不会自动保存。
- 第二页导航名称从“新建复盘 / SGF复盘”改为“分析工作台”，既有图片与手动 SGF 工作流保持不变。

## 验证

- 首页 UI + Agent 定向：`pytest -q test_agent_home.py test_app.py test_review_agent.py` -> **23 passed**。
- 完整非真机套件：`pytest -q` -> **153 passed, 5 skipped**。
- 新测试覆盖 request/context 构造、adapter 关闭、整盘/定点参数传递、一次提交一次调用、rerun 结果复用、
  历史静默/有信号显示、调用计数/trace、controlled partial failure，以及显式点击前绝不保存。
- 自动化测试未调用真实 KataGo 或 OpenAI。

## 真实浏览器与真实 API E2E 前剩余工作

- 使用真实 SGF 在浏览器分别 smoke 整盘、定点和历史目标，确认 KataGo、controller、批量解释、embedding
  的实际配置和耗时，并点击一次“保存到错题本”确认 SQLite 记录出现在错题本页面。
- 本阶段没有修改 ReviewAgent、KataGo、Memory schema、阈值、解释 schema 或 dashboard。

---

# ReviewAgent 低成本高层规划（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- `ReviewAgent` 从“每个工具后再调用 controller”改为一次高层 plan。planner 只选择
  `full_review`、`targeted_review` 或 `memory_review`，以及 Top-K、是否明确询问历史、是否保存；
  依赖明确的 scan/rank/deep/explain/history/save 随后在本地连续执行。
- 正常成功路径不再调用 controller 生成 final；用户答案从 canonical deep evidence、已验证解释和
  history summary 本地渲染，避免再花一次模型调用或让 controller 发明数值。
- controller 失败默认立即返回 `controller_error`；可配置最多 **1** 次重试。失败不会继续消耗
  `max_steps` 重复请求，observation 保留异常类型与消息。
- `ErrorExplanationService.explain_many()` 用一次严格 JSON Schema 请求返回与 evidence 等长、同序的
  explanation；整批 provider/格式失败时每条 evidence 都保留并返回 normalized pending。
- full review 默认在 Top-K 用户错误获得解释后检查个人历史，不需要额外 controller call。语义文本
  一次批量 embedding，然后复用现有 `search_similar_errors()`；排除当前 game、仅个人归属记录、
  尊重双向 `not_same_error` feedback。
- `history_similarity_threshold` 是可配置的展示阈值，默认 0.85，只控制是否向用户显示候选，不是
  围棋判断阈值。无候选时普通复盘不提历史；用户明确询问时说明“暂未找到有意义相似候选”。
- `history_summary` 包含 `key_errors_checked`、`key_errors_with_history_signal`、
  `historical_candidate_count`、`repeated_error_candidates` 及显示阈值。候选始终描述为潜在相似，
  不声称已经确认是同一种错误。
- memory-focused route 使用新增 `list_personal_embedded_events()` 与既有 cosine search 在已存记录间
  找候选，不扫描当前棋局、不调用解释或 embedding provider。
- state/result 增加 `controller_calls`、`explanation_calls`、`embedding_calls`。同一 session 中相同
  scan、同一 selected-error signature 的 deep analysis、成功 explanation、embedding 和 history
  search 均复用；保存可复用已生成向量。

## 当前执行路径与预期模型调用

- 整盘：**1 planner** -> local scan -> local user-color rank -> local deep -> **1 batched explanation**
  -> **1 batched embedding** -> local history search -> optional local save -> local final。
- 定点：**1 planner** -> local target-before/after analysis -> **1 explanation** -> local final；默认不做
  full scan 或 history embedding。
- 历史专用：**1 planner** -> local persisted-memory comparison -> local final；不扫描当前棋局，
  explanation/embedding provider 调用均为 0。
- 改造前整盘通常每个 tool 都有一次 controller 调用，另有逐条 explanation；改造后确定性步骤之间
  controller 调用为 0。

## 验证

- Agent + batch explanation + Memory 定向测试：**36 passed**。
- Agent、Memory、explanation、scan/deep/ranking 与旧 Streamlit 回归：**81 passed**。
- 完整非真机套件：`pytest -q` -> **148 passed, 5 skipped**。
- 测试覆盖三条不同路线、默认历史检查、静默/显式无匹配、候选非确诊措辞、缓存复用、调用计数、
  bounded controller retry、用户颜色和对手错误拒存；未调用真实 API。

## UI 与真实 API smoke 前剩余工作

- Streamlit 创建 `ReviewAgentContext`，提供自然语言目标/目标手/保存授权，复用 session state 保存
  Agent state，并渲染本地 final、history summary、trace 与 partial failure。
- 真实 API 分别 smoke 三个 planner route，并确认目标模型接受 `review_agent_plan` 与 batch
  explanation JSON Schema；记录 controller/explanation/embedding 实际计费调用数。
- 用真实 embedding + SQLite 验证默认历史候选、无候选静默、`not_same_error` 抑制与可选保存。

---

# ReviewAgent 条件工具编排（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- 新增独立 `review_agent.py`。`ReviewAgent` 每一步把结构化 state 与显式 tool schemas 交给
  controller；controller 返回一个 `tool` 或 `final` 决策。工具 observation 回写 state 后才进行
  下一次决策，因此不是预设顺序的固定 pipeline。
- `ReviewAgentState` 保存用户目标、用户颜色、可选目标手、game/review id、scan result、已排名
  事件、deep evidence、解释结果、Memory matches、保存的 event ids、observations、step count 与
  trace。controller 只看到经过整理的结构化 view，不接触 KataGo raw JSON。
- domain tool registry 暴露：`scan_full_game`、`find_key_errors`、`deep_analyze_errors`、
  `analyze_target_move`、`explain_error`、`search_error_memory`、`save_error_memory`。
- `find_key_errors` 复用已有 detection/ranking，并在 Top-K 前按 `user_color` 过滤。
  `save_error_memory` 再次检查 finalized ErrorEvent 的玩家必须等于用户颜色，防止把对手错误写入
  个人 Memory。
- 新的窄范围 `analyze_target_move` 只分析指定手前后两个局面，构造 canonical 黑棋视角的
  `DeepAnalysisEvidence`，不调用全盘 scan；若该手通过现有候选阈值才关联可保存 ErrorEvent。
- 历史检索和保存只能由 controller 显式选择。Memory observation 标记为
  `potentially_similar`；最终答案若仅因非零相似度断言“相同错误”，会被拒绝并要求 controller
  重新决策。
- `OpenAIReviewController` 沿用现有延迟 client，可通过 `OPENAI_REVIEW_AGENT_MODEL` 或构造参数
  配置模型，也支持注入兼容 client。它使用 JSON Schema 决策；本阶段自动测试只用 fake
  controller，没有调用真实 API。
- Registry 拒绝未知工具并严格校验参数。工具异常转为 observation，不使整次 run 崩溃；
  `max_steps` 确定性停止重复调用，结果始终包含轻量 trace 和用户答案/停止说明。

## 已验证的不同执行路径

- 整盘请求：`scan_full_game -> find_key_errors -> deep_analyze_errors -> explain_error`
  `-> controller 选择 search_error_memory -> controller 选择 save_error_memory -> final`。
- 定点请求“只看第 87 手”：`analyze_target_move -> explain_error -> final`；明确未调用
  `scan_full_game`。实际 domain tool 单测验证仅查询目标手前后两个局面并传递 500 visits。
- 另有 controller 路径在 scan 后直接 final，证明 Memory search/save 不会自动发生。

## 验证

- 新 Agent 测试：`pytest -q test_review_agent.py` -> **11 passed**。
- Agent、scan/detection/ranking/deep/explanation/Memory 与旧 Streamlit 回归 -> **76 passed**。
- 完整非真机套件：`pytest -q` -> **143 passed, 5 skipped**。
- fake/mock controller、engine、LLM 与 embedding 覆盖条件路径、用户颜色、未知工具、无效参数、
  工具错误、max-step、trace、对手错误拒存与相似度措辞边界；未调用真实 OpenAI/KataGo。

## 端到端 UI 与真实 API smoke 前的准确剩余工作

- 在 Streamlit review session 中创建/关闭 adapter、构造 `ReviewAgentContext`，把 SGF、用户颜色、
  可选目标手和自然语言目标传入 `ReviewAgent.run()`，并渲染 trace/部分失败/最终答案。
- 使用真实 OpenAI controller 做一次受控 smoke，确认当前模型对动态 JSON Schema 决策的兼容性，
  并分别验证整盘与定点路径；再用真实 explanation/embedding 配置验证 Memory search/save。
- 定义 UI 的保存授权与 pending 重试入口。本阶段没有后台任务、UI CRUD、skill profile、动态阈值
  或 dashboard 改动。

---

# 事件级持久化与相似错误 Memory（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- 在现有 `review_history.db` / `memory_store.py` 中新增规范化事件记忆表；旧 `games` / `issues`
  表、图片错题本保存/读取和 UI 行为保持不变，没有引入第二个数据库。
- 新表为 `memory_games`、`memory_error_events`、`memory_error_evidence`、
  `memory_error_explanations`、`memory_error_embeddings`、`memory_similarity_feedback`。
- `game_memory_id()` 对稳定的原始棋局内容做 SHA-256；`error_memory_id()` 使用 game id、玩家、
  事件范围、候选手和 worst move 生成确定性 id，不使用 LLM 文本。相同棋局和逻辑事件重复保存
  会 upsert 原记录。
- `save_error_memory()` 在一个事务中写 game、ErrorEvent、versioned evidence、解释状态和初始
  embedding 状态。`actual_move` / `recommended_move`、所有 loss/severity、canonical 黑棋数值、
  warnings、schema version 及 created/updated timestamps 分列保存，不保存 KataGo 原始 JSON。
- embedding 生成在上述事务提交后执行。失败只把 embedding 标为 `failed`，不会回滚事件、证据
  或解释；无成功解释时 embedding 保持 `pending` 且无伪造文本。
- `update_error_explanation()` 根据 evidence fingerprint 验证后原位补全 pending 解释，不新增事件。
  `update_error_embedding()` 可显式生成或重试单条向量，但本阶段没有后台任务或自动重试。
- `build_semantic_mistake_text()` 只使用 phase、玩家、实战/推荐着以及已验证解释的五个语义字段，
  不包含胜率、目差、visits 或 raw JSON。
- `search_similar_errors()` 复用 SQLite BLOB + Python cosine similarity，返回排序分数与 Agent 所需
  事件元数据；支持排除当前 event/game，默认排除 `belongs_to_user=False` 的棋局。
- `set_error_importance()` 保存 `is_important`；`set_similarity_feedback()` 保存 event-pair 级
  `same_error` / `not_same_error`，后者会在对应当前事件的后续检索中抑制该历史匹配。

## 当前完整数据流

`ErrorEvent + LLMErrorEvidence v1 + ExplanationResult`
`-> transaction(game/event/evidence/explanation/embedding state)`
`-> optional semantic text -> embedding provider -> second transaction(status/vector)`
`-> search_similar_errors(query vector or stored current-event vector)`
`-> scored historical event metadata (personal ownership/exclusions/feedback applied)`

## 验证

- 新事件 Memory 测试：`pytest -q test_event_memory.py` -> **8 passed**。
- 相关检测、排名、深挖、解释、持久化和旧 Streamlit 错题本回归 -> **65 passed**。
- 完整非真机套件：`pytest -q` -> **132 passed, 5 skipped**。
- 测试只使用临时 SQLite 与 fake embedding；未调用真实 OpenAI / embedding API。

## ReviewAgent orchestration 前的准确剩余边界

- 尚未提供面向 Agent 的 tool schemas / tool registry，以及“何时保存、何时补解释、何时生成向量、
  何时召回”的 controller/workflow。
- 尚未把 SGF 全盘流水线的实际运行结果批量调用 `save_error_memory()`，也未接 UI CRUD。
- similarity 目前只排序并返回分数，不使用未校准阈值宣称“同类错误”；skill profile、动态阈值、
  Memory 摘要与 RAG prompt 注入仍未实现。

---

# KataGo Evidence -> LLM 结构化解释（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- 新增独立 `error_explanation.py`，没有修改 `deep_analysis.py`、KataGo adapter、UI、数据库、
  embedding 或检测/排名启发式。
- `build_llm_evidence()` 将 `DeepAnalysisEvidence` 一对一映射为稳定、可序列化的
  `LLMErrorEvidence`。schema version 为 `go-error-evidence.v1`；所有 KataGo 数值原样保留，
  canonical 指标继续使用黑棋视角，玩家视角 loss 不重新计算。
- `StructuredExplanation` 固定为 `title`、`summary`、`why_it_matters`、`better_plan`、
  `learning_point` 五个非空字符串。数值由 evidence 提供，模型提示明确禁止计算、改写或在解释
  字段中复述精确数值。
- `ErrorExplanationService` 仅接收 `LLMErrorEvidence`，使用现有延迟 OpenAI client 与
  `chat.completions` JSON Schema 约定。模型可通过构造参数或 `OPENAI_EXPLANATION_MODEL`
  配置，client 可注入以支持测试和 OpenAI-compatible provider。
- 响应执行严格形状、字段和非空字符串校验。默认用户语言为中文；prompt 禁止虚构变化、领地、
  棋块、提子、先后手、死活、战术/战略原因或历史用户模式。
- `ExplanationResult` 始终保留原 evidence。成功返回 `status=succeeded` 与已验证解释；配置缺失、
  provider/网络/timeout、无效响应分别返回 `status=pending` 与 `configuration_error`、
  `provider_error`、`invalid_response`，不生成假解释，也不使 KataGo evidence 失效。

## 当前完整数据流

`SGF -> low-visits scan -> MoveAnalysis[] -> ErrorEvent[] -> user-color Top-K`
`-> high-visits DeepAnalysisEvidence[] -> LLMErrorEvidence v1 -> ErrorExplanationService`
`-> ExplanationResult(evidence + validated explanation | pending error)`

LLM 不接触 KataGo 原始 JSON，也不负责生成数值事实。

## 验证

- 新解释层测试：`pytest -q test_error_explanation.py` -> **12 passed**。
- 相关快扫、检测、排名、深挖、解释及 SGF/KataGo 测试 -> **107 passed**。
- 完整非真机套件：`pytest -q` -> **124 passed, 5 skipped**。
- 全部测试使用 fake/mock client；未调用真实 OpenAI API。

## 持久化 / Memory 前的准确剩余边界

- 尚未定义 explanation/evidence 的 SQLite 表、事件身份与幂等键、schema migration、写入事务、
  查询 API，以及 embedding 文本从已验证解释中派生的规则。
- 下一阶段应持久化 `LLMErrorEvidence.to_dict()` 与 `ExplanationResult` 的状态/解释，保留
  `schema_version` 和 normalized `error_code`；pending 可重试，但不得覆盖或重算 KataGo 数值。
- UI、错题本 CRUD、RAG 检索与 Agent orchestration 仍未接入本流水线。

---

# 用户错误事件高 visits 深挖（2026-09-17，未提交实现，以工作树为准）

## 本阶段实现

- `rank_error_events(events, moves, *, user_color, limit=5)` 现在先按 `B` / `W` 过滤用户事件，
  再进行确定性排序和 Top-K 截断；对手错误不会占用用户名额。
- 新增独立 `deep_analysis.py`。`deep_analyze_ranked_events()` 接收已排名的用户事件，针对每个
  事件的 `worst_move_number` 分析落子前后两个局面，默认 `max_visits=500`。
- 同一次深挖按局面编号去重。例如最差着分别为第 1、2 手时，只查询局面 0、1、2，重叠的
  局面 1 不会重复调用 KataGo。
- `DeepAnalysisEvidence` 保留 canonical 黑棋视角的前后胜率与目差，并按事件行棋方重新计算
  `player_winrate_loss` / `player_score_loss`；同时包含 rank、player、phase、事件范围、最差手、
  实战着、深度推荐着、visits 和合并后的 warnings。
- 深挖前会核对 SGF、`MoveAnalysis`、`ErrorEvent`、`RankedErrorEvent` 的手数、玩家、实战着、
  phase 和事件范围。`max_visits` 必须是正整数。
- adapter 返回标准化 `error` / `unavailable` 或缺少必要字段时抛出 `DeepAnalysisError`；不会用
  快扫数据静默替代深挖结果。

## 当前完整流水线

`SGF -> N+1 局面低 visits scan -> MoveAnalysis[] -> ErrorEvent detection -> 按 user_color 过滤`
`-> Top-K RankedErrorEvent[] -> worst move 前/后局面高 visits 去重查询 -> DeepAnalysisEvidence[]`

KataGo 的原始精确指标在快扫与深挖层都保持黑棋视角。玩家视角损失只在业务证据层转换。

## 验证

- 深挖与排名定向测试：`pytest -q test_error_ranking.py test_deep_analysis.py` -> **21 passed**。
- 扫描、阶段、终局、检测、排名、深挖与 SGF/KataGo 相关测试 -> **107 passed**。
- 普通非真机测试套件：`pytest -q` -> **112 passed, 5 skipped**。
- Python compile check 通过；本阶段没有运行真实 KataGo，也没有修改 UI、持久化、LLM 或 RAG。

## KataGo Evidence -> LLM 前的剩余边界

- 尚未定义给 LLM 的稳定 evidence schema / prompt、解释服务、失败边界与结果持久化；下一阶段应
  只消费 `DeepAnalysisEvidence`，不要重新解析 KataGo 原始 JSON。
- detection 的 recovery 检查范围差异仍存在：目前只检查两候选之间同一玩家的中间着法，可能
  跳过由对手着造成的强恢复。阈值、severity 与 phase 规则仍是待校准启发式。

---

# 当前工作树快照（2026-09-17，未提交实现，以工作树为准）

本节覆盖下方较早阶段记录。不要根据旧 commit 假设这些功能尚未存在；先检查 `git status`
并保留当前未提交文件。

## 已核实实现

- 真实 KataGo 与 `PersistentKataGoAdapter` 已存在；全盘扫描继续消费标准化、黑棋视角的
  `AnalysisResult`。
- `scan_game()` 对 N 手棋谱严格分析 N+1 个局面，并生成 N 个 `MoveAnalysis`；默认
  `KATAGO_SCAN_VISITS=100`。
- `MoveAnalysis` 已增加 `stones_on_board`、`occupancy_ratio`、`phase`。棋子数来自
  `game.position(move_number)` 的实际重建盘面，因此提子会减少占用数。
- phase V1：手数 <= 40 且占用率 < 0.18 为 opening；手数 >= 120 且占用率 >= 0.35
  为 endgame；其余为 middlegame。规则明确是待校准启发式，不是围棋真理。
- `GameScanResult.termination` 已实现：根据 SGF `RE` 归类为 `scored`、`resigned`、
  `other_result` 或 `incomplete`；终局类型与 phase 相互独立。
- `error_detection.py` 已存在：候选阈值为行棋方胜率损失 >= 0.05 或目差损失 >= 2.0；
  同色邻近候选可合并，默认允许中间 1 手同色非候选着。
- recovery break 默认阈值为胜率恢复 >= 0.10 或目差恢复 >= 3.0。`ErrorEvent` 已包含
  sum/net/peak losses、worst move 和按候选阈值归一化的临时 `severity_score`。
- `error_ranking.py` 已存在：先按 `user_color` 过滤，再按 severity、peak winrate、peak score、
  较早 start move 确定性排序，默认 Top-5；`RankedErrorEvent.phase` 取 worst move 的 phase。

## 已核实差异 / 下一步边界

- 个人复盘颜色过滤已由上方深挖阶段补齐：`user_color` 是必填 keyword 参数，Top-K 在过滤后
  执行。
- **recovery 检查范围比目标窄**：当前 `_same_event()` 只检查两候选之间“同一玩家”的
  中间着法 before/after；它跳过对手着，也不检查当前候选着的 before。因此由对手着造成、
  且发生在连续同色候选之间的强恢复可能不会切断事件。
- 本轮只同步状态与文档，没有修改 detection/ranking 行为，也没有开始下一个功能。

## 当前验证与 Git 状态

- 相关纯单元测试：
  `pytest -q test_game_scan.py test_phase_detection.py test_game_termination.py test_error_detection.py test_error_ranking.py test_sgf_katago.py`
  → **93 passed**。
- 同步前工作树已有：`.gitignore`、`game_scan.py` 修改；`error_detection.py`、
  `error_ranking.py`、四个对应测试文件及 `html/` 未跟踪。它们是当前工作成果，不能清理、
  覆盖或误认为由本次同步创建。
- 当前分支 `codex/sgf-katago`；最近已提交基线为 `ed41ce9`。本节之后 `HANDOFF.md`
  也处于修改状态。本轮不 push。

---

# SGF 全盘快扫 v1（2026-09-15，已完成）

## 实现

新增 UI 无关的 `game_scan.py`。`scan_game(game, adapter, max_visits=100)` 对 N 手主线严格
调用 N+1 次现有 adapter：初始局面一次，再依次分析第 1 至 N 手后的局面。返回：

- `GameScanResult.position_analyses`: N+1 个原始标准化 `AnalysisResult`，仍全部为黑棋视角。
- `GameScanResult.moves`: N 个 `MoveAnalysis`，第 n 条固定使用 analyses[n-1] 作为 before、
  analyses[n] 作为 after。
- `MoveAnalysis`: `move_number`、`player`（B/W）、`actual_move`（GTP/pass）、
  `best_move_before`、before/after 黑棋胜率与目差、实际行棋方胜率损失与目差损失、
  `visits`（before 分析）、合并去重 warnings。

黑棋行棋损失直接使用黑棋 before-after；白棋先转换为 `1 - black_winrate` 与
`-black_score_lead` 再计算 before-after，因此负损失代表该手让行棋方变好。坐标格式化抽到
共享 `board_state.point_to_gtp()`，adapter 和扫描层共同使用。

SGF UI 的 `sgf_user_color` 值为 `B` 或 `W`，仅影响“你的胜率/目差”、扫描表格的用户视角列
和本人标记。改变用户颜色不会清除或重新调用单局面/全盘引擎结果。引擎标准结果继续保持
黑棋视角。全盘按钮在一次点击内创建 `PersistentKataGoAdapter`，顺序扫描后在 `finally`
关闭，不把进程跨 Streamlit rerun 缓存。

`KATAGO_SCAN_VISITS` 可通过进程环境或项目 `.env` 配置，缺省和预期生产快扫值均为 **100**；
`.env.example` 已记录。真机自动测试特意使用 10 visits，没有运行长棋谱 benchmark。

## 验证

- 普通 `pytest -q`: **64 passed, 5 skipped**。
- 20 手 mock SGF：严格 **21** 次调用，位置序号 0–20，产生 21 个 position results / 20 个
  MoveAnalysis；before/after、手数、实战着、黑白损失、pass 和默认 100 visits 均通过。
- AppTest：白棋展示将黑棋 60% / +2.3 转为用户 40% / -2.3；切换用户颜色不增加调用；
  4 手 UI 扫描严格调用 5 次并关闭 adapter。
- 真机 integration: **4 passed**。短扫描棋谱 10 手、11 个局面，使用 10 visits；11 次查询
  PID 均为 **22860**，返回 11 个有效标准结果和 10 条 MoveAnalysis；shutdown 后
  `process.poll()` 非空，无遗留进程。
- Edge / Streamlit / 真实 KataGo E2E: **1 passed**；单局面流程适配用户视角标签。
- `compileall` 与 Git whitespace check 通过。

## 100 手手工验证

在 PowerShell 进入项目后执行：

```powershell
$env:KATAGO_SCAN_VISITS='100'
.\start.bat --server.port=8501 --server.address=127.0.0.1
```

浏览器中进入“新建复盘 / SGF复盘” → 上传 100 手 SGF → 选择黑棋或白棋 → 点击
“扫描全局 · 100 visits”。完成提示必须为“已完成 101 个局面、100 手”，表格必须为 100 行。
关闭启动窗口后可执行 `Remove-Item Env:KATAGO_SCAN_VISITS` 清除当前 PowerShell 的覆盖值。

## 下一步

先记录上述 100 手人工测试的总耗时和失败位置；通过后再单独设计关键错误排序与 Top-K，
本阶段没有预埋排名或深挖逻辑。

---

# PersistentKataGoAdapter（2026-09-15，已完成）

## 实现

`PersistentKataGoAdapter` 实现现有 `KataGoAdapter.analyze(position, max_visits=...)`
协议并复用 `build_query()` / `normalize_output()`。它只负责持久 Analysis Engine 进程：

- 第一次查询启动一次 `katago.exe analysis`，保持 stdin/stdout；后续串行查询复用进程。
- `threading.Lock` 覆盖整次 analyze，单调递增的 `position-N` 为每次查询提供唯一 id。
- 后台 reader 只把 stdout 行送入队列；调用线程按每次查询自己的 deadline 等待匹配 id，
  忽略其他 id 和搜索中间结果，保留匹配查询的 warning。
- timeout、断管、stdout 提前关闭、非 JSON 输出或已死亡进程抛出 `KataGoProcessError`，
  同时清空并终止当前进程；下一次 analyze 可以重新启动。
- 引擎正常返回的规则/请求错误仍转换为 `AnalysisResult(status='error')`；配置缺失仍返回
  `unavailable`。结果结构和黑棋视角语义没有改变。
- `close()` / `shutdown()` 幂等地关闭 stdin 并终止进程。`pid` 属性用于生命周期验证。
- `LocalKataGoAdapter` 行为不变，只与 persistent 实现共享启动路径、超时、运行库校验。

没有修改 UI、数据库 schema、全盘分析或 adapter 选择策略；Streamlit 仍明确使用现有
`LocalKataGoAdapter`。

## 验证

- 普通 `pytest -q`: **60 passed, 4 skipped**。
- persistent 单元测试覆盖：两次查询只启动一个进程、唯一/匹配 response id、忽略其他 id、
  最佳候选空 PV、每查询 timeout 清理、shutdown 终止、死亡进程本次报错后下次重启。
- 真机 integration: **3 passed**。最终代码另行复跑持久测试 **1 passed**；连续查询
  move 2 / move 4 的 PID 均为 **8656**：
  - move 2: winrate 0.977725504，score lead +3.36832486，best F5，visits 154。
  - move 4: winrate 0.998111659，score lead +9.28294545，best F4，visits 154。
- `compileall`、`git diff --check` 通过。

## 下一步

在独立 service/lifecycle 层决定 persistent adapter 的应用级所有权和关闭时机，再接入 UI；
不要在 Streamlit rerun 中无缓存地创建持久 adapter，也不要同时实现全盘扫描。

---

# Streamlit 原型迁移（2026-09-15，已完成）

## 实现状态

静态 `html/` 仅作为布局与交互参考，未复制其中的演示数据，也未纳入提交。应用现使用
Streamlit 原生 `st.navigation` 提供首页、新建复盘 / SGF复盘、错题本、成长看板四个页面。
主题采用克制的“暮色梅影”配色，复盘页在 1366×768 下优先保留棋盘、KataGo Evidence
和 LLM 区域；原型中的虚构 LLM 文本被明确的未接入状态替代。

调用关系保持后端边界：

`app.py` → `app_pages/*` → 既有 `sgf_ingestion` / `KataGoAdapter` / `analyzer` /
`memory_store`。UI 不解析 KataGo 原始 JSON，也不包含 subprocess 细节。快速/深度模式只改变
当前单局面的 `max_visits`（150/500），没有引入 persistent engine、全盘扫描或 Agent。

- 图片入口继续使用真实图片识别、手动棋盘确认、GPT 快速/深度点评、embedding 检索与
  SQLite 保存流程。
- SGF 入口继续使用真实 SGF → Position → LocalKataGoAdapter → AnalysisResult，支持选手数，
  展示胜率、目差、visits、推荐着、候选着和 PV；改变棋谱、手数或模式会清除旧结果。
- 错题本读取既有 SQLite 数据，选中项行内展开；有选中项时其余记录弱化。新增的读取函数
  不加载 embedding、不修改 schema。
- 成长看板只聚合 SQLite 的真实记录；无数据时显示空状态，不生成演示趋势。
- SGF 的“加入错题本”保持禁用并说明原因：当前 schema/流程保存的是 GPT 结构化错误及
  embedding，不能把 KataGo 数值伪装成同类记录。本阶段没有越界实现 Evidence → LLM 或
  事件级 Memory。

## 验证

- `pytest -q`: **56 passed, 3 skipped**（真机与浏览器测试默认可选）。
- `RUN_KATAGO_INTEGRATION=1 pytest -q -s -m integration`: **2 passed**。
  - move 2: winrate 0.976054835，score lead +3.45561815，best F5，visits 504。
  - move 4: winrate 0.998088001，score lead +9.34530935，best F5，visits 504。
- 真实 Edge / Streamlit / KataGo 浏览器 E2E：**1 passed**；覆盖四页导航、SGF 上传、
  move 2 / move 4、真实 150 visits 分析和 LLM 区域可见性。
- AppTest 覆盖图片识别、手动确认、GPT 两阶段调用、SQLite 新增/读取、两条错题的行内展开、
  看板真实聚合、KataGo 快速/深度参数和失败状态。
- `compileall` 与 `git diff --check` 通过；未调用真实 OpenAI 服务。

## 下一步

实现同一 `KataGoAdapter` 协议下的 `PersistentKataGoAdapter`，先处理进程生命周期、query id、
响应匹配与关闭，再复用现有 Position / AnalysisResult 和真机测试。不要同时开始全盘扫描。

---

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
