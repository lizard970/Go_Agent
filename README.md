[README_GoReview_Agent.md](https://github.com/user-attachments/files/32335074/README_GoReview_Agent.md)
# Go Agent

> 面向业余围棋学习者的 **AI Agent 复盘与错题记忆系统**  
> 通过 LLM Planner 理解复盘目标，调用本地 KataGo 完成确定性棋力分析，再结合结构化 Evidence、批量点评与长期错误记忆，帮助用户把“一盘棋的数据”转化为“下一盘真正能改掉的问题”。

---

## 产品定位

很多业余棋手能看到胜率曲线、推荐着和目差，却仍然会遇到三个问题：

1. **不知道哪些手最值得看**：一盘棋上百手，注意力成本很高。
2. **看得懂数字，看不懂原因**：KataGo 会告诉你“哪里亏了”，但不一定告诉初学者“为什么值得在意”。
3. **同样的问题反复出现**：单局复盘结束后，历史错误难以积累成可复用的个人经验。

GoReview Agent 的目标不是再做一个“围棋胜率面板”，而是把复盘过程重构为：

**自然语言目标 → Agent 路由 → KataGo 分析 → 关键度筛选 → LLM 教练点评 → 错题记忆 → 历史相似问题提醒**

---

## Demo 能做什么

### 1. 用自然语言发起不同复盘任务

同一个入口下，用户可以直接说：

- “完整复盘这盘棋，找出最值得改的几个问题。”
- “只分析第 78 手。”
- “我最近是不是总犯类似的错误？”

Planner 会根据目标选择不同执行路径，而不是让所有请求都经过同一条固定 Workflow。

### 2. KataGo 本地扫描整盘，LLM 只处理值得说的部分

完整复盘时，系统先用 KataGo 对整盘进行逐手分析，再在本地计算每手的关注度：

- **quiet**：无需单独展开，不发给 LLM
- **minor**：轻微问题，短评
- **major**：明显问题，中等解释
- **critical**：关键错误，深度解释
- **positive**：有证据支持的高质量落子，可给予正向反馈

这样可以避免“180 手 = 180 次模型调用”。

### 3. 逐手复盘播放器

分析工作台支持：

- 上一手 / 下一手
- 手数 Slider
- 当前棋盘
- 实战着 / 推荐着
- 胜率 / 目差变化
- 当前手的 AI 点评
- Top Error 标记
- 历史相似错误提示
- 关键度时间轴

时间轴高度表示该位置的复盘价值，帮助用户快速找到“值得停下来看的地方”。

### 4. 关键手深度分析

对于重要位置，系统不仅保留：

- 实战着
- KataGo 推荐着
- 胜率 / 目差变化

还会尽可能向解释层提供：

- Top-N 候选着
- Principal Variation（PV）
- ErrorEvent 上下文
- 当前阶段
- 深度分析前后评价

LLM 的职责是**解释证据**，而不是重新计算棋力结论。

### 5. 错题本与长期记忆

系统可保存：

- ErrorEvent
- 对应棋局
- 当时棋盘快照
- 关键手数
- KataGo Evidence
- LLM 点评
- Embedding / 历史相似错误信息

用户可从错题本直接**回到原棋局对应手数**，重新查看当时棋面与分析。

---

## 为什么这是一个 Agent，而不是普通 Workflow

GoReview Agent 采用的是 **受约束的 Planner + Tool Agent** 设计，而不是高成本的开放式 ReAct 循环。

```text
                 ┌─────────────────┐
                 │  用户自然语言目标 │
                 └────────┬────────┘
                          ↓
                 ┌─────────────────┐
                 │   LLM Planner    │
                 │ Intent / Routing │
                 └────────┬────────┘
                          ↓
          ┌───────────────┼────────────────┐
          ↓               ↓                ↓
   Full Review      Targeted Review    Memory Query
          ↓               ↓                ↓
       KataGo          KataGo          SQLite / RAG
          ↓               ↓                ↓
      Evidence        Evidence           Matches
          └───────────────┼────────────────┘
                          ↓
                 LLM Commentary
                          ↓
                    Review Result
```

核心原则是：

> **让 LLM 负责真正需要语义判断的部分，让确定性计算交给代码和 KataGo。**

---

## 核心产品设计

### Evidence-grounded Commentary

LLM 不直接读取一堆原始 KataGo JSON，也不被要求“凭感觉判断棋理”。

系统先把引擎输出转化为结构化 Evidence：

```text
实际落子
推荐落子
胜率变化
目差变化
Top 候选
PV
阶段信息
ErrorEvent
```

再由 LLM 生成面向学习者的自然语言解释。

### Selective Commentary

完整复盘并不是“所有手都调用 LLM”。

```text
quiet → 本地提示，无 API
minor → 简短点评
major → 中等点评
critical → 详细点评
```

正常完整复盘的模型调用目标约为：

```text
1 × Planner
1 × Whole-game commentary batch
1 × Embedding batch（需要历史检索时）
```

### ErrorEvent，而不是逐手机械记错

连续几手可能属于同一个错误思路。系统不会把每一次局面评价下降都当成独立错题，而是通过 ErrorEvent 聚合同一阶段的连续失误，再进行 severity、Top-K、深度分析与错题保存。

### Relative Ranking 与 Absolute Severity 分离

“本局 Top 3”不等于“重大失误”。

系统分别维护：

- `is_top_error`：这盘棋里的相对排名
- `commentary_level`：基于证据的绝对严重程度

避免为了凑 Top-K 而制造夸张结论。

---

## 成本与性能设计

### 本地执行

以下能力均不消耗 OpenAI API：

- SGF 解析
- 棋盘重建
- KataGo 整盘扫描
- 逐手重要度计算
- ErrorEvent 检测
- Top-K 排名
- Timeline 数据
- SQLite 查询
- 页面导航
- 已缓存结果恢复

### API 主要用于

- Planner / Intent Routing
- 教练式自然语言点评
- Embedding

### 缓存策略

昂贵复盘结果会被本地持久化并复用，避免页面刷新、切换页面或重复上传同一局时再次产生模型成本。

---

## 技术架构

### AI / Engine

- **OpenAI API**：Planner、自然语言点评、Embedding
- **KataGo**：围棋局面分析、候选着、PV、胜率 / 目差
- **Structured Output**：保证批量点评可可靠映射回具体手数

### Backend

- Python
- SQLite
- Streamlit
- Persistent KataGo Process
- Semantic Similarity / Embedding Memory

### Product Logic

- Agent Routing
- Tool Calling
- Structured Evidence
- ErrorEvent Detection
- Selective Commentary
- Persistent Mistake Memory
- Cost-aware Orchestration

---

## 主要模块

```text
GoReview Agent
├─ app.py
├─ agent_home.py
├─ review_agent.py
├─ game_scan.py
├─ katago_adapter.py
├─ deep_analysis.py
├─ error_detection.py
├─ error_ranking.py
├─ error_explanation.py
├─ move_commentary.py
├─ memory_store.py
├─ app_pages/
├─ tests/
└─ README.md
```

| 模块 | 作用 |
|---|---|
| `review_agent.py` | Planner 路由、Agent 状态与任务执行 |
| `game_scan.py` | SGF 全盘逐手扫描 |
| `katago_adapter.py` | KataGo 进程与标准化输出 |
| `error_detection.py` | ErrorEvent 检测 |
| `error_ranking.py` | 用户侧关键错误排名 |
| `deep_analysis.py` | 关键位置高 visits 深分析 |
| `move_commentary.py` | Selective Commentary 与批量点评 |
| `memory_store.py` | 错题、棋局、解释、Embedding 等持久化 |
| `app_pages/` | 首页、分析工作台、错题本、成长看板 |

---

## 典型用户流程

### 完整复盘

```text
上传 SGF
→ 选择自己的棋色
→ 输入自然语言目标
→ Planner 选择 full-review route
→ KataGo 整盘扫描
→ 本地 Importance / ErrorEvent
→ Top 错误深分析
→ 批量生成点评
→ 历史相似错误检索
→ 分析工作台逐手复盘
→ 保存错题
```

### 指定手分析

```text
“只分析第 78 手”
→ Planner 选择 targeted-review route
→ 重建目标手前局面
→ KataGo 深分析
→ 候选着 / PV / 评价对比
→ 生成单手深度点评
```

### 历史错误查询

```text
“我最近是不是总犯同类错误？”
→ Planner 选择 memory route
→ SQLite / Embedding 检索
→ 返回历史相似错误
```

---

## 产品界面

当前产品包含四个主要页面：

- **首页**：自然语言 Agent 入口
- **分析工作台**：逐手播放棋局，并同步显示当前局面的 KataGo 与 LLM 评价
- **错题本**：查看历史错误、棋盘快照、深度点评，并可返回原局对应手数
- **成长看板**：展示复盘记录与已有可验证统计指标

---

## 设计取舍

### 为什么不用纯 LLM 看 SGF？

棋力判断是高精度计算任务。直接让 LLM 读取整盘 SGF 并判断优劣会带来棋力结论不稳定、战术幻觉、缺少可校验依据和额外成本。

> **KataGo 负责“发生了什么”，LLM 负责“如何让用户理解这件事”。**

### 为什么不用每一步都调用模型？

大多数落子并不值得单独展开。对初学者而言，更重要的是管理注意力：

> **不是告诉用户更多，而是告诉用户什么值得看。**

### 为什么不用高频 ReAct Loop？

早期多轮 Controller 虽然自主性更高，但 API 调用次数、延迟和失败重试成本也显著增加。最终采用一次高层 Planner 决策 + 本地确定性执行，在 Agent 能力、成本与可控性之间取平衡。

---

## 本地运行

### 1. Clone

```bash
git clone https://github.com/lizard970/Go_Agent.git
cd Go_Agent
```

### 2. 创建环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 配置

本项目需要：

- OpenAI API Key
- KataGo executable
- KataGo model
- KataGo analysis config

本地密钥、模型文件、数据库和用户棋谱缓存不应提交到 GitHub。

### 4. 启动

```powershell
streamlit run app.py
```

---

## 测试

项目包含自动化测试覆盖：

- ReviewAgent 路由
- 全盘 / 定点分析
- ErrorEvent
- Deep Analysis
- Selective Commentary
- Memory / Persistence
- Streamlit 交互
- API 调用预算
- Cache / Session State
- 错题保存与历史读取

测试默认使用 Mock / Fake Provider，避免回归测试产生真实 API 成本。

---

## 当前状态

### 已实现

- [x] 自然语言 Planner / Tool Routing
- [x] 本地 KataGo 持久进程
- [x] SGF 全盘逐手分析
- [x] ErrorEvent 检测与 Top-K
- [x] 关键位置深度分析
- [x] Structured Evidence
- [x] Selective Commentary
- [x] 定点单手复盘
- [x] 逐手棋盘播放器
- [x] Importance Timeline
- [x] SQLite 错题记忆
- [x] Embedding 历史相似错误检索
- [x] 错题棋盘快照
- [x] 错题回到原局
- [x] 结果缓存与复用

### 后续方向

- [ ] 本地温柔 / 中性 / 严厉语气渲染
- [ ] 更完善的围棋错误类型 taxonomy
- [ ] 基于真实棋谱的阈值校准
- [ ] 更严格的 Prompt / Benchmark Evaluation
- [ ] 用户棋力画像与长期趋势建模

---

## 项目亮点

从 AI 产品设计角度，本项目重点不在“调用了一个大模型”，而在于：

- 如何划分 LLM 与专业引擎的职责
- 如何将自然语言需求转化为工具路由
- 如何把引擎输出转成可解释 Evidence
- 如何控制模型调用成本
- 如何设计长期错误记忆
- 如何让分析结果真正进入学习闭环

它更接近一个可落地的 Agent 产品，而不是单次 Prompt Demo。

---

## Author

**lizard970**

GitHub: https://github.com/lizard970

---

## License

This project is currently intended for portfolio / educational demonstration use.  
Please review third-party licenses for KataGo and any external models or assets before production deployment.
