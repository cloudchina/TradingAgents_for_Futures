# 分析系统「记忆体系」建设方案（短期记忆 / 长期记忆 / 品种关联）

> 目标：让每次分析不再是"一次性失忆推理"，而是能参考历史结论、历史对错、以及关联品种（热卷↔螺纹、焦煤↔焦炭、PX↔PTA）当前状态，形成连续、可校准、可复盘的决策链。

---

> ## 【三评修订说明 v3】（2026-09-17 / 2026-09-18 续）
>
> 本文件已并入第三轮评审意见。三评改动以 **【三评】** 标记，整段改写以 **【三评改写】** 标记。
> 三评定为两类改动：**P0 字段名口径** 与 **A-L 次要补漏**。
>
> ### P0：Executive agent 输出字段名口径与中英文/数值翻译映射
>
> 背景：Executive prompt（[system_prompts.py#L141-L148](file:///d:/NeatDownload/futures-trading-web/backend/agents/prompts/system_prompts.py#L141)）
> 同时输出中英文两套字段（`final_decision` 英文方向 / `directional_view` 中文方向 / `directional_confidence` 数值 / `confidence_level` 中文等级），
> 一评曾误把外层 key `executive_decision` 当成方向字段，二评沿用此误，会导致 episode 落库时
> `result["executive_decision"]["direction"]` 永远 KeyError、每条都打 `parse_failed`、记忆库空转。
>
> ### A-L：次要补漏（共 12 条）
>
> | 编号 | 主题 | 落地章节 |
> |---|---|---|
> | A | `parse_failed` 时 `outcome` 列取值约定 | 四、阶段 1 schema |
> | B | `invoked_count += 1` 触发判据（= ContextBuilder 写入 prompt） | 二.2.B 降级判据 |
> | C | `memory_refs` 只能引用本次 prompt 实际注入过的 id | 二.4.4 |
> | D | `reliability` 三层 fallback（品种→全局→常量 0.5） | 二.4 四分量 |
> | E | `record_insight` 单次 run 上限 2 次 | 四、阶段 3 |
> | F | 埋点落点 `memory_injections` 表 + `episodes.injected_tokens` | 四、阶段 6 |
> | G | 阶段 0 全品种更新并发 + 断点续传 | 四、阶段 0 |
> | H | 多元组（≥3）不在 yaml 定义 `lead`，由 `relation_metrics` 按 pair 算 | 二.3 yaml 注释 |
> | I | 本地开发 `DATA_ROOT_DIR`/`MEMORY_DIR` 需在 `.env` 改写 | 八、修改清单 |
> | J | ECE 阈值 ≥ 0.05 / 分档偏差 ≤ 0.10 / 命中率提升 ≥ 5pp | 七、验收指标 |
> | K | 21:00 调度入口 `ScheduledBackfillRunner` | 四、阶段 4 |
> | L | hook 粒度衔接（episode 品种粒度 / 回填任务粒度） | 四、阶段 4 |
>
> ### 映射约定（贯穿全文档）
>
> - `direction`（判据）← `final_decision` 英文 `long/short/neutral`；`direction_view`（展示）← `directional_view` 中文
> - `confidence`（判据）← `directional_confidence` 0.0~1.0；`confidence_level`（展示）← `confidence_level` 中文
> - 数值→中文：`>=0.7→高`、`0.5~0.7→中`、`<0.5→低`；英文↔中文：`long↔看多`、`short↔看空`、`neutral↔中性`
>
> 三评暂未触动二评的其他结论；二评决策 1~4 继续有效。

---

> ## 【二评修订说明 v2】（2026-09-17）
>
> 本文件已并入第二轮评审意见与需求方决策。所有二评改动以 **【二评】** 标记，新增的第二次评审整段改写以
> **【二评改写】** 标记。四项决策：
>
> | 决策 | 结论 | 影响章节 |
> |---|---|---|
> | 1. 技术面数据覆盖 | 数据由「数据更新」页面按需下载，**不是阻塞项**；新增 **阶段 0（数据盘点与补齐）** 作为前置 | 三.4、四.阶段0、七 |
> | 2. 换月处理 | 复盘收益一律用 **后复权连续序列** 计算（与指标口径一致），`contract` 降级为**审计字段**，**不再因换月弃样本**，删除 `unverifiable_rolled` | 三.1、三.2、六、十.3 |
> | 3. 语义记忆准入 | `draft → active` 允许走 **二次 LLM 校验自动通道**，人工 review 为可选覆盖（阶段 5 上线） | 二.2.B、四.阶段4 |
> | 4. 关联方向 | 关联关系为 **无序对称关系**，以"当前被分析品种"为锚点取对端；方向由 `lead_symbol` + `lag_days` 表达 | 二.3、四.阶段2、十.2 |
>
> 其他二评修订：`analyst_only` 模式不写 episode、agent 输出 `parse_failed` 兜底（防脏数据污染记忆）、
> `MEMORY_DIR` 配置化适配 Docker 命名卷、`is_canonical` 部分唯一索引、关联段独立于总分排序、
> 语义记忆降级判据量纲修正、交易日历改为 `akshare`、动态相关增加最小样本与显著性门槛。

---

## 一、现状与问题（基于现有代码）

当前链路：`routers/analysis.py` → `services/analysis_service.py::AnalysisManager._analyze_commodity`
→ `agents/debate/orchestrator.py`（Bull/Bear/Macro）→ Trader / RiskManager / CIO（均为 `ReActAgent`）。

现状痛点：

| 问题 | 代码现状 |
|---|---|
| 无跨次记忆 | 结果只落到 `cache_service` 的 `{symbol}_{date}.pkl`，仅供"同日重复查询"命中缓存，agent 完全看不到 |
| 无跨品种关联 | 每个品种独立跑一遍辩论，`commodities.yaml` 只有 `category` 大类，没有产业链/替代/套利关系 |
| 无对错反馈 | 分析产出就结束，`executive_decision` 有没有说对永远没人回填，置信度无法校准。**【评审补充 + 三评改写】** 一评曾写"原文误写为 `final_decision`，实际字段为 `executive_decision`"（[analysis_service.py#L223](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L223) `_run_executive_decision`）——此修正在层级上有误：`executive_decision` 是 `result` dict 的**外层 key**，其值才是 Executive agent 输出的 JSON。Executive 同时输出中英文两套字段（[system_prompts.py#L141-L148](file:///d:/NeatDownload/futures-trading-web/backend/agents/prompts/system_prompts.py#L141)），episode 落库时需做翻译映射：<br>① `direction` ← `result["executive_decision"]["final_decision"]`（英文 `long/short/neutral`，归一存储）；<br>② `confidence` ← `result["executive_decision"]["directional_confidence"]`（0.0~1.0 数值）；<br>③ 中文展示需要时另取 `directional_view`（`看多/看空/中性`）与 `confidence_level`（`高/中/低`），但**不作为判据字段**；<br>④ `parse_failed` 判据：`result["executive_decision"]` 缺 `final_decision` 或 `directional_confidence`，或 `ReActAgent` 返回 `{"raw": ...}`（[react_agent.py#L171-L193](file:///d:/NeatDownload/futures-trading-web/backend/agents/react_agent.py#L171)） |
| 观点漂移无解释 | 今天看多明天看空不需要任何说明，用户无法判断可靠性 |
| token 浪费 | 每次都从零读 CSV 重新推理，历史已验证的结论无法复用 |
| **【二评】agent 输出无 schema 校验** | `ReActAgent._safe_json_parse` 是宽容解析，失败时返回 `{"raw": text}`（[react_agent.py#L171-L193](file:///d:/NeatDownload/futures-trading-web/backend/agents/react_agent.py#L171)），`direction`/`confidence` 可能整体缺失。这是记忆污染的最大入口，比 LLM 巩固幻觉更常见 |
| **【二评】非完整流程无决策链** | `_analyze_commodity` 仅在 `analysis_mode == "complete_flow"` 时才产出 trader/risk/executive（[analysis_service.py#L218-L228](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L218)），`analyst_only` 模式没有方向/置信度可落库 |

---

## 二、记忆体系总体设计（三层 + 一图谱）

```
                    ┌──────────────────────────────────────┐
                    │  记忆检索与注入层 MemoryContextBuilder │  ← token 预算裁剪 + 打分排序
                    └───────────────┬──────────────────────┘
                                    │ 拼装成一段「记忆上下文」
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌───────────────┐        ┌────────────────────┐       ┌──────────────────┐
│ 短期记忆 STM   │        │ 长期记忆 LTM        │       │ 品种关联图谱      │
│ 近 5~10 交易日 │        │ 情景记忆 + 语义记忆 │       │ Relation Graph   │
│ 未验证预测/观点 │        │ 复盘统计/规律沉淀   │       │ 静态产业链+动态相关│
└───────────────┘        └────────────────────┘       └──────────────────┘
        ▲                           ▲                           ▲
        └───────────── 分析完成后自动写入 ←──────────────────────┘
                       复盘回填 Job（定时）
```

### 1. 短期记忆（Short-Term / Working Memory）

- 时间窗：**最近 N 个交易日（默认 `stm_window = N = 10`）** 的品种级"分析快照"滚动队列。
- 内容（每条 episode 摘要，非全文）：
  `日期 / 方向 / 置信度 / 核心依据（≤3 条）/ 决策动作 / 当时价格 / 是否已验证`
- 另含 **待验证预测队列（pending forecasts）**：本次结论即入队，**horizon ∈ {5, 10} 交易日**。
  **【二评】** 术语区分：STM 的 `stm_window=10` 是"回看窗口"，forecast 的 `horizon∈{5,10}` 是"预测步长"，
  两者数值相近但语义不同，代码与文档中禁止混用同一常量名。
- 作用：让 agent 知道"我上次说了什么、依据是什么、变没变"，强制观点连续性。

### 2. 长期记忆（Long-Term Memory，两类）

**A. 情景记忆（Episodic）**：所有历史 episode 全量落库（SQLite），带 `outcome` 回填结果：
`hit(方向是否对)、realized_return、MAE(最大不利偏移)、holding_days`。
按 `exp(-λ·Δt)` 时间衰减 + 命中率加权，只把 top-k 注入 prompt。

**B. 语义记忆（Semantic）**：由"记忆巩固 Job"用 LLM 从多条 episode 中归纳出的**可复用规律**，例如：
- `RB：库存去化 + 基差走强 → 后续 5 日上涨，样本 7 次，命中 5 次`
- `PX：加工费低于 X 时 PTA 成本支撑显著（关联传导，滞后 1~2 日）`
- `JM/J：焦煤领先焦炭约 1 日（实测 lag=+1, corr=0.88）`
带 `category(规律/季节性/结构/风险)、evidence_count、confidence、source_episodes`，支持去重与冲突消解（新证据与旧规律矛盾时降权或归档）。

**【评审补充 + 二评修订】语义记忆状态机与防幻觉机制**：LLM 巩固出的"规律"本身可能幻觉，必须有状态与阈值控制：

- 状态流转：`draft`（LLM 刚产出，未校验）→ `active`（已通过校验，可注入 prompt）→ `archived`（被证伪或被新规律替代，停止注入）
- `draft → active` 准入：`evidence_count >= 3` 且 `confidence >= 0.5` 且（**二次 LLM 校验通过** OR **人工 review 通过**）
  - **【二评 / 决策 3】默认走自动通道**：阶段 4 起即由"二次 LLM 校验"自动转 `active`，
    人工 review 通道随阶段 5 前端上线后作为**覆盖手段**（可强制回退 `draft`、可从 `archived` 恢复）。
    若强制等待人工界面，阶段 4 会长期处于"0 条 active 语义记忆"的空转状态。
- **`confidence` 口径（二评新增，必须显式定义，否则二次校验无从判定）**：
  `confidence = 0.5 × (命中 episode 数 / 支撑该规律的 episode 数) + 0.5 × LLM 自评置信度`；
  两个分量分别写入 `semantics.stat_confidence` 与 `semantics.llm_confidence`，合成值写 `semantics.confidence`。
- 注入 prompt 时硬过滤：`WHERE status='active' AND evidence_count >= 3 AND confidence >= 0.5`
- 降级判据 **【二评修正：原分母量纲错误】**：语义记忆每被注入一次 `invoked_count += 1`；
  注入到失败分析（`outcome.hit=False`）时 `failed_invocations += 1`；
  当 `invoked_count >= 5` 且 `failed_invocations / invoked_count > 0.5` 时自动降级为 `archived`。
  - **【三评】"被注入"判据**：`invoked_count += 1` 的触发点是 **ContextBuilder 把该 semantic 写入本次 prompt**，
    与 agent 是否在 `memory_refs` 中回引无关；否则 agent 不回引时永远少计，降级统计不准。
    ContextBuilder 在拼装 prompt 时把本次注入的 semantic_id 集合一并写入 `memory_injections` 表（见阶段 6 埋点），
    回填 outcome 后由 `outcome_service` 比对"本次注入的 semantic_id × outcome.hit"批量累加 `invoked_count`/`failed_invocations`。
  - 原写法 `failed_invocations / evidence_count > 0.5` 分子是"注入后失败次数"、分母是"归纳该规律用的 episode 数"，
    两者不是同一总体，逻辑不成立；
  - 且"一次分析失败 ≠ 这条记忆有错"，故要求最小注入次数 ≥ 5 才触发降级，避免小样本误杀。
- `archived` 状态不参与注入，但仍保留在库中便于审计与人工 review 恢复。

### 3. 品种关联图谱（Relation Graph）

**【二评 / 决策 4】关联关系是无序对称的**：以"当前被分析品种"为锚点取对端。
分析 HC 时 RB 是关联方，分析 RB 时 HC 是关联方——同一条关系记录互为镜像，**不重复存储、不重复计算**。
方向信息（谁领先谁）由 `lead_symbol` + `lag_days` 表达，与"谁被查询"解耦。

存储规范化：`pair_key = "-".join(sorted([a, b]))`（如 `"HC-RB"`），查询时取所有包含当前品种的 `pair_key` 并取对端。

**静态部分** `backend/config/commodity_relations.yaml`（可人工维护）：

```yaml
# members 为一组关联品种（2 个或更多），加载时展开为 C(n,2) 条无序对并生成 pair_key。
# strength 必填（缺省回退 0.5，禁止为 0——related_relevance 是乘式，0 会让整条边消失）。
# lead 仅用于二元组（members 长度=2），标注哪一端领先；lag_days 相对 lead 定义（lead 领先 |lag| 日）。
# 【三评】多元组（长度≥3）不在此定义 lead——单 lead 只能表达线性链，无法表达三品种无清晰 lead-lag 的情形；
# 多元组的方向关系由 relation_metrics 按每条 pair 单独算 lag 后写入 lead_symbol/lag_days。
relations:
  - members: [RB, HC]
    type: substitute      # 替代/套利
    strength: 0.9
    note: 卷螺价差套利，螺纹地产需求、热卷制造业需求
  - members: [JM, J]
    type: upstream        # 焦煤 → 焦炭（原料→成品）
    strength: 0.9
    lead: JM              # 焦煤领先焦炭约 1 日
  - members: [PX, TA]
    type: upstream        # PX → PTA
    strength: 0.85
    lead: PX
  - members: [I, RB]
    type: upstream        # 铁矿 → 成材成本
    strength: 0.75
  - members: [Y, P, OI]
    type: substitute      # 油脂替代（三元组 → Y-P / Y-OI / P-OI）
    strength: 0.8
  - members: [SC, FU, LU, BU]
    type: chain           # 原油产业链（四元组 → 6 条无序对）
    strength: 0.8
  - members: [AU, AG]
    type: macro           # 宏观同向
    strength: 0.8
  - members: [FG, SA]
    type: chain           # 玻璃-纯碱
    strength: 0.8
  - members: [L, PP, V]
    type: chain           # 聚烯烃
    strength: 0.75
  - members: [M, RM]
    type: substitute
    strength: 0.85
```

**动态部分**：用现有 `data/qihuo/database/technical_analysis/<SYMBOL>/ohlc_data.csv` 的收盘价算**日收益率**。
**【评审补充】** 原文写法 `*.csv` 过于宽泛，实际为单文件 `ohlc_data.csv`，见 [data_service.py#L66-L72](file:///d:/NeatDownload/futures-trading-web/backend/services/data_service.py#L66) `MODULE_CONFIG["technical_analysis"]` 的 `data_file: "ohlc_data.csv"`。

计算 **滚动相关系数（60/120 日）+ 交叉相关领先滞后 lag ∈ [-3, +3]**，写入 `relation_metrics` 表，定期（每周/每次数据更新后）刷新。

**【二评】三条硬约束（原方案缺失，否则会产出噪声污染图谱）**：

1. **方向约定写死**：`relation_metrics` 增加 `lead_symbol` 字段，`lag_days` 定义为
   "`lead_symbol` 领先另一端的交易日数"（`lag=+1, lead=JM` 即 JM 领先 J 1 日）。
   禁止用 `(a, b)` 的书写顺序隐式表达方向，否则 `(RB,HC)` 与 `(HC,RB)` 会重复且 lag 符号矛盾。
2. **最小样本门槛**：`n < 60` 不计算 60 日相关；`n < 150` 不计算 120 日相关与 lag。
   未达门槛只落静态关系，`corr_source="static_only"`。（当前落盘数据中 JD 仅 13 行、MA 7 行，必然触发）
3. **显著性门槛**：lag 仅在 `|corr| >= 0.3` 时才写入 `lead_symbol` 与 `lead_hint`；
   60 日窗口上算 lag∈[-3,+3] 极易过拟合，不设门槛会向 prompt 注入"JM 领先 J 1 日"这类无统计支撑的结论。

静态关系给"先验"，动态相关给"当下实测"，二者融合后排序注入。

**注入方式**：分析 RB 时，上下文里附带
`HC(替代 corr=0.93) 近5日 +1.2%，最近结论 看多 conf=0.6（9-14）；I(原料 corr=0.81) 最近结论 看空 conf=0.55（9-12）→ 与 RB 当前倾向分歧，注意交叉验证`。

**【二评】方向文案按锚点翻译**（同一条关系，两种展示）：

- 分析 **J** 时：`JM（上游原料，领先 J 约 1 日，corr=0.88）…`
- 分析 **JM** 时：`J（下游成品，滞后 JM 约 1 日，corr=0.88）…`

并且给 agent 提供 `get_related_symbols` / `search_memory` 工具，让它**自己决定**要不要深挖（省 token）。

### 4. 记忆检索与注入（Context Builder）

打分：`score = 0.35·recency + 0.25·relevance(本品种 > 关联品种) + 0.25·reliability(命中率/证据数) + 0.15·importance`
→ top-k + 总长度上限。

**【二评】四个分量必须给出可实现定义（原方案未定义，公式无法落地）**：

- `recency = exp(-Δt / λ)`，`Δt` = 距今交易日数，`λ = stm_window / 2 = 5`（窗口边缘 10 日衰减到 ≈0.135）
- `relevance`：本品种固定 `1.0`（保证 > 任何关联品种）；关联品种见下方 `related_relevance`
- `reliability`：episode 取该品种历史命中率（无样本时取全局默认 0.5）；语义记忆取 `confidence`
  - **【三评】三层 fallback 口径**：`reliability = 该品种历史命中率`（样本 ≥ N 时）→
    `该品种历史命中率`不足 N 时取 `全局所有 resolved episode 命中率`→
    全局也无 resolved episode 时取常量 `0.5`；
    N 建议取 5（与降级判据最小注入次数对齐），避免单次样本即决定 reliability。
- `importance = 0.5 × (|confidence − 0.5| × 2) + 0.5 × action_score`，
  `action_score`：明确开仓/平仓建议 = 1.0，观望 = 0.5，无明确动作 = 0.3

**预算单位改为 token**（原"字"与 LLM 计费口径不一致）：短期 ≤ 800 tokens、长期 ≤ 600、关联 ≤ 400，
实现上以 `len(text)` 作为保守上界（中文 1 字 ≈ 1~1.5 token），并在日志中记录实际注入 token 数，供阶段 6 度量。

**【评审补充】关联品种 `relevance` 子公式细化**：
```
related_relevance = relation_strength_static × |corr_dynamic| × recency_factor
```
- `relation_strength_static`：来自 `commodity_relations.yaml` 的 `strength`（0~1）
- `corr_dynamic`：来自 `relation_metrics.corr`（无动态相关时回退为 `relation_strength_static`，并标 `corr_source="static_only"`）
- `recency_factor = exp(-Δt / τ)`，`Δt` 为关联品种最近 episode 距今交易日数，`τ=10`（与 STM 窗口对齐）
- **【二评】关联段独立排序，不参与总分竞争**：`related_relevance` 是三项连乘（典型值 0.3~0.5），
  再乘 0.25 权重会被"本品种 relevance=1.0"碾压。故关联品种单独按 `related_relevance` 降序取 top-3 后
  拼进"关联段"，不与本品种 episode 一起参与 `score` 排序——两套逻辑不得混用。
- 注入时按 `related_relevance` 降序取 top-3 关联品种，避免关联段膨胀超预算
- 本品种自身 `relevance` 固定为 1.0（保证 > 任何关联品种）

注入点：

1. `DebateOrchestrator.__init__` 增加 `memory_context` 参数 → Bull/Bear **每轮** prompt 带上。
   **【二评】** `orchestrator._run_bull/_run_bear` 是每轮重新拼 `user_msg`
   （[orchestrator.py#L142-L179](file:///d:/NeatDownload/futures-trading-web/backend/agents/debate/orchestrator.py#L142)），
   只在 `__init__` 存一次极易退化成"仅 Round 1 有记忆"。实现要求：记忆段作为 `user_msg` 的**固定前缀**
   （或写入 system prompt），每轮都带。
   `check_consensus`（Macro 裁判，**另一次 LLM 调用**）**不注入记忆**——裁判职责是判定多空谁更有理，
   注入记忆会引入额外锚定，保持其独立性。
2. `_run_trader / _run_risk_management / _run_executive_decision` 的 `user_msg` 追加记忆段；
3. `system_prompts.py` 各角色增加规则：**"记忆仅供参考，最新数据优先；若本次观点与上次相反，必须在输出中给出 `change_reason`"**，输出 JSON 增加 `change_vs_last`、`memory_refs` 字段。
4. **【二评 + 三评】`has_prior` / `memory_refs` 由服务端补齐，不由 agent 自填**：
   `has_prior` 是 ContextBuilder 已知的事实，让 agent 回写等于多开一个幻觉面 → **落库时由服务端写入**；
   `memory_refs` 若由 agent 输出，服务端需校验后只保留有效引用（无效 id 直接丢弃，不报错）。
   - **【三评】引用类型与校验范围**：`memory_refs` 只允许引用**本次 prompt 实际注入过的** episode/semantic id
     （ContextBuilder 已有注入 id 列表，校验是 O(1) 集合差集）；
     禁止 agent 引用任意历史 id——否则可编造"我参考了 9-12 某条记忆"污染审计链。

**【评审补充】首日场景处理**：首次分析某品种时无历史 episode，强制要求 `change_vs_last` 会让 agent 编造理由。补字段：
- 输出 JSON 增加前置标志 `has_prior: bool`（由 ContextBuilder 根据是否查到上次 episode 写入，agent 不自行判断）
- `has_prior=false` 时 `change_vs_last` 取值固定为 `"first_run"`，prompt 规则放宽为"若 `has_prior=false`，无需给出 change_reason"
- `has_prior=true` 且方向相反时才强制 `change_reason` 非空，避免无历史时硬塞编造内容污染记忆

---

## 三、复盘闭环（记忆的关键价值：让系统知道自己错在哪）

1. 每次分析产出时记录一条 **forecast**：`symbol, analysis_date, direction, confidence, ref_price, contract, horizon(5/10 交易日)`，状态 `pending`。

   **【二评 / 决策 2】两个字段的语义变更**：
   - `contract`：分析时的主力合约代码（如 RB2501），取自 `commodity_service.get_dominant_contract(symbol)`。
     由原方案的"换月污染判据"降级为**审计字段**——记录"当时看的是哪个合约"，用于人工复盘与口径说明，
     **不再作为是否计入校准统计的依据**（见第 2 步）。
   - `ref_price`：落库时的参考价，仅用于展示与人工核对。**复盘回填禁止使用它**（见第 2 步"复权漂移"）。

   **【二评 + 三评】写入前置校验（防脏数据污染记忆）**：
   - `analysis_mode != "complete_flow"`（如 `analyst_only`）**不写 episode**——该模式不产出
     trader/risk/executive，无 `final_decision`/`directional_confidence` 可落库；
   - **【三评】字段映射（中英文翻译 + 数值翻译成中文）**：Executive agent 输出同时含中英文两套字段，
     episode 落库统一用"英文 + 数值"作判据存储、"中文 + 等级"作展示：
     - `direction`（判据）← `result["executive_decision"]["final_decision"]`（英文 `long/short/neutral`，归一存储）；
     - `direction_view`（展示）← `result["executive_decision"]["directional_view"]`（中文 `看多/看空/中性`）；
     - `confidence`（判据）← `result["executive_decision"]["directional_confidence"]`（0.0~1.0 数值）；
     - `confidence_level`（展示）← `result["executive_decision"]["confidence_level"]`（中文 `高/中/低`）；
     - 数值→中文等级的统一映射（用于其他角色回写或服务端补齐）：
       `confidence >= 0.7 → "高"`、`0.5 <= confidence < 0.7 → "中"`、`confidence < 0.5 → "低"`；
     - 英文↔中文方向统一映射：`long↔看多`、`short↔看空`、`neutral↔中性`。
   - **`parse_failed` 判据**：`result["executive_decision"]` 缺 `final_decision` 或 `directional_confidence`，
     或 `ReActAgent` 返回 `{"raw": ...}` 时，仍写 episode 但置 `status="parse_failed"`，
     **不进入 STM 注入、不进入校准统计**，仅作审计留痕。

2. 定时任务（`scheduled_service` 每日跑，或分析任务启动前顺带跑）用最新 `technical_analysis` 收盘价回填：
   `realized_return = (P(t+h) - P(t)) / P(t) * sign(direction)`、`hit`、`MAE`。

   **【二评改写】口径统一：一律使用后复权连续序列（决策 2）**

   `technical_analysis/<SYMBOL>/ohlc_data.csv` 的数据源是 `ak.futures_main_sina`（主力连续，品种+0），
   且 `technical_updater.py` 在保存前已调用 `MainContractSync.apply_rollover_adjustment()` 做**后复权**
   （[technical_updater.py#L789-L801](file:///d:/NeatDownload/futures-trading-web/backend/modules/technical_updater.py#L789)），
   依据本地主力合约库 `main_contract/<SYMBOL>/dominant_contract.csv` 确认换月点。
   也就是说这份序列**本身就是为跨合约连续比较而设计的**，与技术指标口径完全一致。

   因此：
   - 收益计算直接在该复权序列上取 `P(t)` 与 `P(t+h)`，**跨换月不再视为不可用样本**；
   - `episodes.outcome` 保留 `rolled: bool` / `actual_contract`，但仅作**审计与人工复核**信息
     （前端/导出可按 `rolled` 筛选"样本期内跨换月"的 episode），**不影响 `hit`、命中率、校准统计**；
   - **删除原方案的 `outcome.status="unverifiable_rolled"` 弃样逻辑**：主力合约 1~2 月换一次，
     horizon=10 交易日撞上换月的比例很高，弃样会同时打爆第七章"resolved 占比 >80%"与
     "≥30 个 resolved 样本"两条门槛，使校准长期不可用。

   **【二评】复权漂移：`P(t)` 必须回查 CSV，禁止用 `ref_price`**
   `apply_rollover_adjustment` 每新增一个换月点就反向累乘 factor 并**整体重写 CSV 历史价格**，
   故 `episodes.ref_price` 会被后续复权"漂移"失效。回填实现要求：`P(t)` 与 `P(t+h)`
   **同时从当前 CSV 读取**，`ref_price` 只留作展示。测试用例见 10.3。

   **【二评】P0 前置：确保复权真正生效**
   复权依赖本地主力合约库覆盖对应日期；当前该库每品种仅 1~2 行（2026-09-07 起），`rollover_cnt=0`，
   现有 CSV 实际**未复权**、换月点仍有真实跳空。故 P0 动作调整为：
   **① 每日"刷新主力合约"任务常开（阶段 0 起）→ ② 积累后触发一次全量复权重算**
   （重跑 `technical_updater.update_to_date` 全量一次即可重建复权序列）。
   前几个月复权质量是渐进改善的，已在第六章风险表显式记录。

   **【二评改写】交易日历 + 换月审计（原"换月弃样"方案已废弃）**：
   - **交易日历**：horizon 是"交易日"而非自然日，必须用交易日历计算 t+h
     （`akshare.tool_trade_date_hist_sina`，**本地缓存一份**避免每次联网；原方案写的 `tushare.trade_cal`
     需额外 token，不引入）。春节/国庆 10 个交易日可能跨 14+ 自然日，按自然日偏移会导致 hit/MAE 全错。
   - **换月审计（不弃样）**：在 t+h 日查询当前主力合约 `contract_t_h`
     （复用 `MainContractSync._read_local(symbol)` 按日期取，**不新增抓取逻辑**）：
     - `contract_t_h == contract_t`：正常回填，`rolled=False`
     - `contract_t_h != contract_t`：`rolled=True`、`actual_contract=contract_t_h`，
       **仍然正常计算并计入校准统计**（价格在复权连续序列上可比）
   - `episodes.outcome` JSON 结构：`{hit, realized_return, MAE, holding_days, rolled: bool, actual_contract: str, unverifiable_reason: "no_csv"|"none"}`
     （`unverifiable_reason` 的 `"rolled"` 取值随弃样逻辑一并删除）
   - 数据缺失品种（尚未下载 technical CSV）标 `unverifiable_reason="no_csv"`，不参与校准。

3. 统计并沉淀：
   - 品种级 / 全局 **方向命中率**
   - **置信度校准曲线**（conf 0.5-0.6/0.6-0.7/0.7-1.0 各档实际胜率）→ 反哺 prompt（"你历史上 conf>0.7 时胜率仅 0.42"）
   - **典型错误模式**（如"低库存单因子看多"失败案例）→ 写入长期记忆作为"避坑提示"
4. 数据缺失品种降级：只记结论不回填 outcome，标 `unverifiable_reason="no_csv"`，不参与校准统计。

   **【二评 / 决策 1】** 数据缺失是"尚未在「数据更新」页面下载该品种"的临时状态，**不是设计约束**：
   阶段 0 会触发一次全品种数据更新把 59 个品种的 `ohlc_data.csv` 铺满；之后再出现 `no_csv`
   视为异常（下载失败 / 新增品种），应在前端数据页给出提示。
   关联图谱（阶段 2）计算动态相关性同样依赖此文件，无 CSV 的品种只能使用静态关系、
   不写 `relation_metrics`，注入时显式标注 `corr_source="static_only"`。

---

## 四、工程落地计划（分阶段，每阶段可独立验收）

### 阶段 0：数据盘点与补齐（0.5 天，**前置**）

**【二评新增】** 后续所有阶段的地基。交付物是一份"数据可用性清单"，同时作为阶段 2/4 的输入与第七章验收基线。

1. 在「数据更新」页面触发一次 **全品种技术面更新**，把 59 个品种的 `ohlc_data.csv` 铺满；
   - **【三评】并发与断点续传**：59 品种串行调 akshare + 复权 + 写 CSV 估计数小时，单点失败要重跑全程。
     实现要求：① 按 symbol 维度断点续传（已成功的品种跳过，依据 `data_availability.json` 已有行数判定）；
     ② 失败列表收集后单批重试（最多 2 次，仍失败则进 `failed_symbols` 列表，前端可一键重试）；
     ③ 并发上限 4~8 品种并行（akshare 限速，过高会触发反爬）；前端显示进度。
2. 输出可用品种矩阵：`品种 × 行数 × 起止日期 × 是否有主力合约历史`，落到
   `data/qihuo/memory/data_availability.json`（并可由 `GET /api/memory/availability` 查询）；
3. 开启/执行一次**刷新主力合约**，并在后续每日同步中**常开**（这是复权生效的前提，见第三章第 2 步 P0）；
   主力合约库积累后触发一次全量复权重算；
4. 落地最小样本门槛常量（`<60` 不算 60 日相关、`<150` 不算 120 日相关与 lag），供阶段 2 直接使用。

### 阶段 1：记忆存储层（1~1.5 天）

新增 `backend/services/memory_service.py` + `backend/models/memory.py`

- **【二评】存储路径配置化**：新增 `settings.MEMORY_DIR`（默认 `<DATA_ROOT_DIR>/../memory`，
  即 Docker 内 `/app/data/qihuo/memory`）与 `get_memory_path()`，SQLite 落 `memory.db`。
  `docker-compose.yml` 已把 `futures-data` 命名卷挂到 `/app/data`，该路径自动被持久化；
  **禁止硬编码相对路径**，否则容器重建丢库。
- SQLite（标准库 `sqlite3`，零新增依赖），表：
  - `episodes`（id, symbol, analysis_date, run_id, created_at, direction, direction_view, confidence, confidence_level, ref_price, contract, summary, key_evidence JSON, decision JSON, horizon, status, outcome JSON, is_canonical, invoked_count, failed_invocations）
    - **【评审补充】** 增补 `run_id`、`is_canonical`、`contract`
    - **【三评】** 增补 `direction_view`（中文 `看多/看空/中性`，展示用）、`confidence_level`（中文 `高/中/低`，展示用）——
      `direction`/`confidence` 仍是判据字段，`direction_view`/`confidence_level` 用于前端展示与人工复核
    - **【二评】** `status` 值域固定为 `pending | resolved | unverifiable | parse_failed`；
      `analyst_only` 模式**不写 episode**（无方向/置信度），`executive_decision` 解析失败写 `parse_failed`
    - **【三评】`outcome` 列取值约定**：`status=pending` 时 `outcome=NULL`；
      `status=resolved` 时 `outcome` 为完整 JSON（`hit/realized_return/MAE/holding_days/rolled/actual_contract`）；
      `status=unverifiable` 时 `outcome={"unverifiable_reason":"no_csv"}`；
      `status=parse_failed` 时 `outcome=NULL`（判据缺字段本身就是问题，不进 outcome；`parse_failed` 不入 `compute_stats()` 分母）。
    - **【二评】** `run_id` 直接复用 `AnalysisTask.task_id`（已是 uuid）+ commodity，不另造概念
    - **【二评】** 唯一约束：`CREATE UNIQUE INDEX idx_episodes_canonical ON episodes(symbol, analysis_date) WHERE is_canonical=1;`
      （SQLite 支持部分索引；没有它，"并发写只有一个 canonical"只能靠应用层事务，测试 10.1 几乎必然失败）
      + `CREATE INDEX idx_episodes_sym_date ON episodes(symbol, analysis_date DESC);`
  - `semantics`（id, symbol, claim, category, evidence_count, confidence, stat_confidence, llm_confidence, first_seen, last_seen, sources JSON, status, invoked_count, failed_invocations, auto_activated_at, reviewed_by）
    **【评审补充 + 二评】** 增补 `failed_invocations` / `invoked_count`（降级判据修正见二.2.B）、
    `stat_confidence` / `llm_confidence`（confidence 口径）、`auto_activated_at` / `reviewed_by`（决策 3 自动通道审计）
  - `relation_metrics`（pair_key, a, b, window, corr, lead_symbol, lag_days, corr_source, sample_size, updated_at）
    **【评审补充 + 二评】** 增补 `corr_source`（"static_only" / "dynamic"）、
    `pair_key`（无序对规范化）、`lead_symbol`（方向约定）、`sample_size`（最小样本门槛判定）
  - `notes`（人工标注 / 手动记忆）
  - `consolidation_runs`（run_id, started_at, finished_at, input_episode_ids JSON, last_episode_id, output_semantic_ids JSON, model, token_usage, status, reviewer）
    **【评审补充】** 审计表；**【二评】** 增补 `last_episode_id` 水位线（防重复触发，见阶段 4）
  - `schema_version`（version INT, applied_at TEXT）
    **【评审补充】** 用于表结构演进时的迁移管理；启动时执行 `migrate()`，按版本号 apply 增量 ALTER，避免后期手工改表造成脏数据。
- **【评审补充 + 二评】** SQLite 连接初始化时执行：
  ```sql
  PRAGMA journal_mode=WAL;      -- 支持并发读 + 单写，远优于单连接锁
  PRAGMA synchronous=NORMAL;   -- WAL 模式下安全且更快
  PRAGMA busy_timeout=5000;    -- 【二评】WAL 下写-写仍会 database is locked，需超时+重试
  PRAGMA foreign_keys=ON;
  ```
  - **【二评】每线程/每请求独立连接**：`sqlite3` 默认 `check_same_thread=True`，
    分析 daemon 线程与 API 线程不能共用全局连接；实现为 `contextmanager` 按需开连接。
  - **【二评】并发压力口径修正**：`AnalysisManager._run_analysis` 是**串行**遍历品种
    （[analysis_service.py#L140](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L140)），
    真正的并发场景是"分析线程写 + 定时任务/API 读"，原方案"多品种并发写入"的提法与代码不符。
- 同时导出 `memory_export.json` 便于人工查看与备份。
- 修改 `analysis_service._analyze_commodity`：完成后**同步写入一条 episode**（含二.三.1 的前置校验）。
  **【评审补充】** 幂等策略调整：
  - 原"同 symbol+date 覆盖"会丢失同日多次跑（不同 `debate_rounds`/不同 model/`force_refresh`）的对比信息，不利于阶段 6 的 A/B 评测。
  - 改为：每次分析生成 `run_id`，**保留所有 run 不覆盖**；同 symbol+date 的最新一次或 `force_refresh=True` 的 run 标 `is_canonical=1`，旧 run 置 `is_canonical=0`（不删除）。
  - STM/LTM 读取时只取 `is_canonical=1` 的 run 注入 prompt，但所有 run 都可被 API 查询/对比。
  - **与 cache 交互**：`cache_service.load_cache` 命中时（[analysis_service.py#L148](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L148)）直接 `continue`，**不会走 `_analyze_commodity`，因此 episode 写入只发生在真正跑分析时**；记忆读取来自 SQLite，与 cache 是否命中无关——这是设计预期，缓存命中复用旧分析结果时无需重写 episode。

### 阶段 2：品种关联图谱（1 天）

- 新增 `backend/config/commodity_relations.yaml`（静态关系，覆盖黑色/能化/油脂/贵金属等主力对子）
- 新增 `backend/services/relation_service.py`：读取静态关系 + 计算动态相关性/领先滞后 → 融合打分
- 复用 `services/data_service.py::MODULE_CONFIG` 的路径解析读取 technical CSV
- **【二评】对称性实现要求**：
  - 加载时把 `members` 展开为 C(n,2) 条无序对，`pair_key = "-".join(sorted([a, b]))`，去重后落库；
  - 查询接口 `get_related(symbol)` 取所有 `pair_key` 含该品种的记录并取对端——分析 HC 得 RB、分析 RB 得 HC，
    互为镜像、同一条记录；
  - `lag_days` 注入文案按锚点翻译（"领先当前品种 N 日" / "滞后当前品种 N 日"）；
  - 未达最小样本门槛或显著性门槛时只落 `corr_source="static_only"`，不写 `corr` / `lead_symbol`；
  - `strength` 缺失回退 0.5（**不得回退 0**）。

### 阶段 3：检索与注入（1.5~2.5 天，核心）

- 新增 `backend/services/memory_builder.py`：`build_context(symbol, date, budget)` → 分段文本
  （**【二评】** 返回结构需含 `has_prior: bool`，由服务端持有，不依赖 agent 回写）
- 新增 `backend/agents/tools/memory_tools.py` + 注册到 `agents/tools/tool_specs.py`：
  - `search_memory(symbol, days, top_k)` 查历史结论与复盘
  - `get_related_symbols(symbol)` 查关联品种及其实测相关/当前信号（**【二评】** 按对称性取对端）
  - `record_insight(symbol, claim, category)` 让 agent 主动沉淀语义记忆（**【二评】** 落库一律 `status='draft'`）
    - **【三评】频次限制**：单次 analysis run 内 `record_insight` 调用上限 **2 次**（由 `memory_tools` 拦截超额调用并记日志），
      防止 agent 在一次分析里反复产 draft 污染 `semantics` 表；超额返回错误"单次分析最多沉淀 2 条规律"。
- 修改 `agents/debate/orchestrator.py`（**【二评】** 记忆段进每轮 `user_msg` 固定前缀，非仅 Round 1）、
  `analysis_service` 三个角色调用、`system_prompts.py`（新增记忆使用规则与输出字段）
- `AnalysisRequest` 增加 `use_memory: bool = True`（前端可关，便于 A/B 对比）
  **【二评】** 同时需要：`scheduled_service.py` 构造 `AnalysisRequest` 时透传该字段，
  并决定 `ScheduledConfig` 是否增加 `use_memory` 及定时任务的默认值（**建议默认 True**）。

### 阶段 4：复盘回填与记忆巩固（1~2 天）

- 新增 `backend/services/outcome_service.py`：`backfill_outcomes()`（回填到期的 forecast）、`compute_stats()`（命中率/校准/错误模式）
  - **【二评】** `backfill_outcomes()` 的价格一律从 `ohlc_data.csv` 实时读取（`P(t)` 与 `P(t+h)` 同一口径），
    **禁止使用 `episodes.ref_price`**（复权漂移）；换月只写 `rolled=True` 审计标记，不弃样。
- 新增 `backend/agents/prompts/memory_prompts.py`：`CONSOLIDATION_PROMPT`（LLM 归纳语义记忆、去重、冲突消解）
- 接入 `scheduled_service.py`：每日 21:00 回填；巩固改为事件驱动；也可手动触发
  **【二评】** 回填挂在 `AnalysisManager.register_completion_hook` 或任务启动前更稳妥——
  `ScheduledAnalysisRunner` 目前只在 `schedule_time` 触发一次分析，另起 21:00 需要独立的调度入口或并入 cron。
  **【三评 / 决策 K + L】具体方案**：
  - **粒度衔接**（L）：episode 写入在 `_analyze_commodity` 内（**品种粒度**，见 [analysis_service.py#L357](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L357)），
    立即落库立即可读；回填（outcome）挂在 `register_completion_hook`（**任务粒度**，[analysis_service.py#L78-L88](file:///d:/NeatDownload/futures-trading-web/backend/services/analysis_service.py#L78)），
    所有品种跑完后批量回填到期 episode——两者粒度不同但衔接 OK，文档显式声明此设计。
  - **21:00 调度入口**（K）：新增 `ScheduledBackfillRunner`（独立 thread + Event，每日 21:00 触发 `backfill_outcomes()`），
    与 `ScheduledAnalysisRunner` 并列启动（在 `main.py` startup 里同时 start）；
    或并入 `apscheduler`/cron 统一调度（目前未引入，需评估引入成本）。
    阶段 4 落地前必须二选一，否则回填无法定时触发。
- 定时任务/启动参数：`run_scheduled_task.py` 增加 `--memory-backfill` / `--memory-consolidate`

**【评审补充 + 二评】巩固 Job 防幻觉与成本控制**：
- **产出状态**：巩固 Job 写入的语义记忆一律先置 `status='draft'`，按第二章 B 节状态机流转，禁止 LLM 产出直接 `active`。
  **【二评 / 决策 3】** 默认由二次 LLM 校验自动转 `active`；人工 review 通道随阶段 5 上线。
- **触发方式改事件驱动**：仅当某品种新增 episode 数 ≥ 5（可配置）时触发该品种的巩固，避免无新证据时的空跑。
  **【二评】** 必须记录 `consolidation_runs.last_episode_id` 水位线（或给 episode 打 `consolidated` 标记），
  否则同一批 episode 会被重复触发归纳——仅记 `input_episode_ids` 无法保证触发幂等。
- **批量 prompt**：单次 LLM 调用喂多品种的 episode 摘要，让模型一次性归纳多条规律，复用 cache 降本；产出后按 `symbol` 拆分写入 `semantics` 表。
- **审计表**：`consolidation_runs` 记录 input/output/model/token_usage/reviewer，便于追溯"这条规律是哪次巩固产出的、用了哪些 episode、谁 review 的"。
- **去重与冲突消解强化**：巩固时先 `embedding(claim)` 与库内 `active`/`draft` 规律算 cosine 相似度，> 0.85 视为重复，merge 而非新增；与新证据矛盾的旧规律标 `conflict_flag=True` 等人工 review，不自动归档（避免误杀）。

### 阶段 5：API 与前端（1~2 天）

- 新增 `backend/routers/memory.py`：
  - `GET  /api/memory/symbols/{symbol}` 品种记忆详情（短期/长期/关联/统计）
  - `GET  /api/memory/relations` 关联图谱（含实测 corr）
  - `GET  /api/memory/stats` 命中率、校准曲线、recent episodes
  - `GET  /api/memory/availability` **【二评】** 数据可用性清单（阶段 0 产出）
  - `POST /api/memory/note` 人工写入/修正记忆；`DELETE /api/memory/{id}`
  - `POST /api/memory/backfill` / `POST /api/memory/consolidate` 手动触发
  - `POST /api/memory/semantics/{id}/review` **【二评】** 语义记忆人工覆盖（强制 draft / 恢复 archived，决策 3）

  **【评审补充】FastAPI 路由声明顺序**：`GET /api/memory/{symbol}` 是路径参数路由，会把 `relations`/`stats` 当作 `symbol` 值匹配。**【二评】已采用推荐方案**：统一改为 `/api/memory/symbols/{symbol}` 前缀彻底规避，不再保留裸 `{symbol}` 路由。
- 前端新增 `views/MemoryView.vue`（品种记忆卡片、关联图、命中率/校准图表、语义记忆列表可编辑/归档/人工 review），
  `api/index.js` 增加接口，`router/index.js` 增加路由，`AnalysisConfig.vue` 增加"启用历史记忆 / 参考关联品种"开关。
- **【二评】补漏**：Word/邮件报告渲染（`scheduled_service.save_analysis_word_report`）若按固定字段清单渲染，
  需同步支持 `change_vs_last` / `memory_refs` / `has_prior`，否则新字段不会出现在报告里。

### 阶段 6：评测与灰度（持续）

- 同一批品种跑 `use_memory=true/false` 对比：观点一致性、命中率、置信度校准误差（ECE）
- 观察 token 消耗与耗时增长，调 top-k 与预算
- **【二评】** 度量口径需固定：以"单次完整分析（含辩论+三角色）的总 token"为准，
  在 `memory_builder` 埋点记录每次注入的 token 数，否则第七章"平均 token 增幅 <15%"无法回归验证。
  - **【三评】埋点落点**：新增 `memory_injections` 表
    `（run_id, symbol, injected_at, segment TEXT /*stm|ltm|relation*/, episode_ids JSON, semantic_ids JSON, token_count INT, char_count INT）`，
    每次 `build_context()` 调用后写一条；同时 `episodes` 表加 `injected_tokens INT` 列冗余记录本次 episode 关联的总注入 token。
    降级统计（见二.2.B `invoked_count` 判据）也依赖此表批量累加，不依赖 agent 回写。

---

## 五、预期提升（价值清单）

1. **观点连续性与可解释性**：强制输出 `change_vs_last` + 变化理由，杜绝"无解释的反复横跳"。
2. **跨品种交叉验证**：分析螺纹时自动参考热卷/铁矿/焦煤焦炭，出现分歧时降低置信度或给出套利/背离信号（这是人工交易员天然会做、当前系统完全缺失的能力）。
3. **产业链传导**：PX 成本上行 → PTA 成本支撑；焦煤走强 → 焦炭跟涨（实测 lag），提前而不是事后反应。
4. **置信度校准**：用历史胜率反哺，抑制"永远 0.8 置信度"的过度自信，让 conf 真正可用于仓位决策。
5. **错误不再重犯**：失败案例（含当时依据）作为负面记忆注入，形成"避坑清单"。
6. **品种画像沉淀**：长期自动形成每个品种的"规律手册"（季节性、结构性矛盾、关键阈值）。
7. **降本**：已验证的近期结论可被引用复用，减少重复数据读取与推理轮次（配合记忆工具按需检索）。
8. **可复盘可审计**：每条决策都能查到"当时为什么这么判、事后对没对"，为后续策略优化提供数据。

---

## 六、风险与对策

**【二评】** 增加"等级 / 责任阶段"列，便于逐条对照验收。

| 风险 | 等级 | 责任阶段 | 对策 |
|---|---|---|---|
| 记忆污染（错误结论被反复引用） | 高 | 1/4 | 所有记忆标注 outcome 与来源；命中率低/已证伪的降权或归档；语义记忆须带 `evidence_count` |
| **【二评】agent 输出解析失败导致脏 episode** | 高 | 1 | `direction`/`confidence` 缺失时落 `status="parse_failed"`，不注入、不进校准；日志告警，人工可查 |
| 锚定效应（被历史观点绑架，忽视新数据） | 中 | 3 | prompt 明确"最新数据优先"；观点改变只需给出理由，不惩罚；冲突时要求显式说明 |
| token 成本与延迟上升 | 中 | 3/6 | 分层预算（token 计）+ top-k + 摘要化；默认只注入短期记忆 + 关联信号，长期记忆按需用工具查 |
| 关联品种结论陈旧 | 中 | 3 | 注入时带结论日期，超过 N 天标记 `stale`，不参与强判断 |
| 部分品种无 technical CSV | 低 | 0 | **【二评 / 决策 1】** 属"未下载"的临时状态；阶段 0 全量更新铺满，之后出现 `no_csv` 视为异常并在数据页提示 |
| 与 `force_refresh` / 缓存交互 | 中 | 1 | 记忆写入与分析同生命周期，与 cache 命中无关；`force_refresh` 重跑不覆盖旧 episode，改为新增 `run_id` 并把新 run 置 `is_canonical=1`、旧 run 置 `is_canonical=0`（不删除），保留对比数据用于评测 |
| SQLite 并发读写 | 中 | 1 | WAL + `busy_timeout=5000` + **每线程独立连接** + 写事务重试；`is_canonical` 用部分唯一索引兜底 |
| **【二评改写】主力换月** | 中 | 4 | 不再弃样：收益一律在后复权连续序列上计算（与指标口径一致），`contract`/`rolled` 仅作审计；**P0 前置是"主力合约库每日同步 + 触发一次全量复权重算"**，否则序列未复权、换月点仍有跳空 |
| **【二评】复权重写历史价格导致收益漂移** | 中 | 4 | 回填时 `P(t)`/`P(t+h)` 均从 CSV 实时读取，禁用 `episodes.ref_price`；测试覆盖"复权重算后重跑回填结果一致" |
| **【二评】复权质量渐进改善** | 中 | 0/4 | 本地主力合约库从 2026-09-07 起积累，`rollover_cnt` 前期偏低；随每日同步逐步覆盖，需在记忆页展示"复权覆盖率" |
| LLM 巩固语义记忆幻觉 | 高 | 4 | 产出一律 `status='draft'`；`evidence_count>=3` + `confidence>=0.5` + 二次 LLM 校验（默认自动通道）/人工 review 才转 `active`；注入时硬过滤 `status='active'`；`invoked_count>=5` 且失败率 >0.5 自动 `archived` |
| 首次分析无历史导致 `change_vs_last` 编造 | 中 | 3 | 输出 JSON 加 `has_prior: bool`（**服务端补齐**）；`has_prior=false` 时 `change_vs_last="first_run"`，prompt 放宽无需 `change_reason` |
| 同日多次分析覆盖丢对比数据 | 中 | 1 | 不覆盖，改为 `run_id` + `is_canonical`，保留所有 run；仅最新或 `force_refresh` 的为 canonical，旧 run 不删，便于 A/B 评测 |
| Schema 演进无版本管理 | 低 | 1 | 加 `schema_version` 表，启动时执行 `migrate()` 按版本号 apply 增量 ALTER，禁止手工改表 |
| 巩固 Job token 成本失控 | 中 | 4 | 事件驱动（新增 episode ≥ 5 才触发）+ `last_episode_id` 水位线防重复；批量 prompt 一次喂多品种；记录 `consolidation_runs.token_usage` 监控成本 |
| **【二评】动态相关小样本过拟合** | 中 | 2 | 最小样本门槛（60/150 行）+ 显著性门槛（\|corr\| ≥ 0.3 才写 `lead_symbol`） |
| **【二评】容器重建丢记忆库** | 中 | 1 | `MEMORY_DIR` 配置化并落在 `futures-data` 命名卷内，禁止硬编码相对路径 |

---

## 七、验收指标（建议）

- **【二评】前置条件**：阶段 0 已完成全品种数据更新，验收所用品种（建议用 **JM / EG / UR / LH / PK**
  等阶段 0 确认有足量历史数据的品种；原方案用 RB，但 RB 当前无 technical CSV，需先更新数据后才可用）
  的 `ohlc_data.csv` 行数 ≥ 150。
- 功能：分析目标品种时能在结果/日志中看到"上次结论 + 关联品种信号 + 历史命中率"被注入；
  分析 RB/HC 互为镜像（查 RB 出现 HC、查 HC 出现 RB，同一条关系记录）。
- 数据：连续运行 2 周后，**可回填品种**中 `status=resolved` 占比 > 80%（`parse_failed` 不计入分母），命中率/校准曲线可查。
- 质量：`use_memory=true` 相比 `false`，方向命中率提升（或 ECE 显著降低），
  平均 token 增幅控制在 15% 以内（**口径：单次完整分析总 token，由 `memory_builder` 埋点统计**）。
  - **【三评】ECE 阈值**：所谓"显著降低"= ECE 绝对值降幅 ≥ 0.05，或置信度分档（0.5-0.6 / 0.6-0.7 / 0.7-1.0）实际胜率与标注置信度偏差 ≤ 0.10。
    命中率"提升"= 同一批品种 resolved episode 命中率提升 ≥ 5 个百分点。低于此阈值视为未达预期。
- 前端：记忆页面可查看、编辑、归档任意品种记忆与关联关系；语义记忆支持人工 review 覆盖。

---

## 八、涉及文件清单

**新增**
- `backend/models/memory.py`
- `backend/services/memory_service.py`、`memory_builder.py`、`relation_service.py`、`outcome_service.py`
- `backend/agents/tools/memory_tools.py`
- `backend/agents/prompts/memory_prompts.py`
- `backend/routers/memory.py`
- `backend/config/commodity_relations.yaml`
- `backend/tests/memory/`（**【二评】** 仓库当前无任何测试目录，需同步新增 `pytest.ini` / 根 `conftest.py`
  并把 `pytest` 加入 `requirements.txt`，注意 venv 与 `Dockerfile` 同步）
- `frontend/src/views/MemoryView.vue`
- `data/qihuo/memory/`（SQLite + JSON 导出，**【二评】** 路径由 `settings.MEMORY_DIR` 决定）

**修改**
- `backend/core/settings.py`（**【二评】** 新增 `MEMORY_DIR` + `get_memory_path()`）
  - **【三评】本地开发场景**：`DATA_ROOT_DIR` 默认 `/app/data/qihuo/database` 是容器内路径，本地开发（Windows/macOS）必须在 `backend/.env` 同步配置 `DATA_ROOT_DIR` 指向本地路径；否则 `Path('/app/data/...')` 在 Windows 上会被解析为绝对路径 `C:\app\data\...`，SQLite 写入失败。
    `MEMORY_DIR` 跟随 `<DATA_ROOT_DIR>/../memory` 也必须改写。建议在 `backend/.env.example` 中放占位条目。
- `backend/services/analysis_service.py`（分析前构建记忆上下文、分析后写 episode、写入前置校验）
- `backend/agents/debate/orchestrator.py`（接收 memory_context 并**每轮**注入）
- `backend/agents/tools/tool_specs.py`（注册记忆工具）
- `backend/agents/prompts/system_prompts.py`（记忆使用规则 + 输出字段 `change_vs_last`/`memory_refs`）
- `backend/models/analysis.py`（`AnalysisRequest.use_memory`）
- `backend/services/scheduled_service.py`（回填/巩固任务、**【二评】** `use_memory` 透传、**【二评】** Word 报告新字段渲染）
- `backend/run_scheduled_task.py`（`--memory-backfill` / `--memory-consolidate`）
- `backend/main.py`（注册 memory 路由）
- `frontend/src/api/index.js`、`router/index.js`、`views/AnalysisConfig.vue`

---

## 九、建议的推进顺序

**【二评】阶段 0（数据盘点与补齐）→ 阶段 1+3（最小可用闭环）→ 阶段 4（复盘校准）→ 阶段 2（关联图谱）→ 阶段 5（前端可视化）→ 阶段 6（评测调优）**

**【评审补充 + 二评】** 原方案顺序为 `1+3 → 2 → 4 → 5 → 6`，评审先建议把阶段 4 提前到阶段 2 之前，二评再在最前面加阶段 0，理由：

0. **【二评】阶段 0 先行**：阶段 2/4 都依赖 `ohlc_data.csv` 与主力合约历史库；当前 59 个品种目录中
   只有 7 个有数据、主力合约库每品种仅 1~2 行。不先补齐，阶段 4 一上来就是空跑，复权也不生效。
1. **可靠性维度无数据可用**：阶段 3 的打分公式含 `reliability(命中率/证据数)` 维度，没有回填的 outcome 时该维度恒为 0，等于自废一臂；先做阶段 4 才能让 reliability 维度真正生效。
2. **关联图谱的"动态相关"不依赖 outcome 但需要历史价格数据**：阶段 2 计算滚动相关系数与领先滞后 lag 用的是 technical_analysis 价格 CSV，与 outcome 无关，可以晚一点启动；但越早开始积累 episode + outcome，做阶段 2 时校准曲线已可用，注入的关联品种结论也能带 reliability 维度。
3. **最小样本门槛**：置信度校准曲线与"避坑提示"语义记忆都需要一定样本量（建议 ≥ 30 个 resolved episode）才有统计意义，越早启动越早达到门槛。
4. **阶段 1+3 跑 1~2 周积累 episode + outcome 后再开阶段 2**，关联图谱注入时 reliability 维度已有数据，避免返工重算。

**前置依赖**：阶段 4 的回填逻辑含交易日历与复权序列口径（见第三章第 2 步【二评改写】），这是 P0 必做项，
不能为了赶进度跳过——否则所有命中率统计会被未复权的换月跳空污染。

---

## 【评审补充 + 二评】十、测试计划

> 原方案六个阶段均未提测试策略，本节为评审补充。测试用例随对应阶段实现同步编写，禁止"先实现完所有阶段再补测试"。
> **【二评】** 仓库当前无测试目录：需先落地 `pytest` 依赖、`pytest.ini`（`testpaths = tests`）与根 `conftest.py`，
> 并在 CI 中作为阶段验收的硬门禁。

### 10.1 `memory_service` 单元测试（阶段 1）

- **写入幂等性**：同 symbol+date+run_id 写两次，记录数不翻倍；同 symbol+date 不同 run_id 写两次，两条都在，`is_canonical` 标记正确（最新或 force_refresh 的为 1）。
- **并发写不丢数据**：起 5 个线程并发写不同 symbol 的 episode，全部落库无丢失；起 5 个线程并发写同 symbol+date 不同 run_id，全部落库且只有一个 `is_canonical=1`（**依赖部分唯一索引，见阶段 1 schema**）。
- **WAL 模式生效**：连接初始化后 `PRAGMA journal_mode` 返回 `wal`；并发读 + 写无 `database is locked` 错误。
- **【二评】线程隔离**：分析 daemon 线程与 API 线程各开独立连接，验证无 `check_same_thread` 异常；验证 `busy_timeout` 已设置。
- **【二评】写入前置校验**：`analysis_mode="analyst_only"` 时**不写 episode**；`executive_decision` 缺 `direction`
  时落 `status="parse_failed"`，且不出现在 `build_context()` 的 STM 段与 `compute_stats()` 中。
- **schema 迁移**：在 `schema_version=1` 的库上跑 `migrate()` 到目标版本，字段/索引增量 apply 成功；已是目标版本再跑 `migrate()` 为 no-op。
- **cache 交互**：mock `cache_service.load_cache` 返回命中，确认不会触发 episode 写入；mock 返回未命中走 `_analyze_commodity` 后，episode 落库。

### 10.2 `relation_service` 单元测试（阶段 2）

- **lag 计算正确性**：用 synthetic 数据构造 A 领先 B 1 日的序列（A[t] = B[t+1] + noise），验证 `lag_days` 接近 +1 且 `lead_symbol=A`、`corr` 显著为正；构造 A 滞后 B 2 日，验证 `lag_days ≈ -2`。
- **【二评】对称性与方向约定（P0）**：
  - `pair_key` 规范化：`(RB,HC)` 与 `(HC,RB)` 生成同一个 `pair_key="HC-RB"`，只落一条记录；
  - 查 `get_related("HC")` 返回 RB，查 `get_related("RB")` 返回 HC，`corr` 一致；
  - 同一条记录的 lag 在两端展示符号相反（查 J 得"JM 领先 1 日"，查 JM 得"J 滞后 1 日"）。
- **【二评】多元组展开**：`members: [Y, P, OI]` 展开为 3 条无序对；`[SC, FU, LU, BU]` 展开为 6 条；
  `strength` 缺失时回退 0.5 而非 0。
- **【二评】最小样本与显著性门槛**：13 行数据不计算 60 日相关（`corr_source="static_only"`、不写 `corr`）；
  100 行数据计算 60 日但不计算 120 日/lag；`|corr| < 0.3` 时不写 `lead_symbol`。
- **静态关系回退**：对无 technical CSV 的品种对，验证 `corr_source="static_only"`，且 `corr` 为 NULL、`lead_symbol` 为空。
- **滚动窗口刷新**：构造 200 日数据，验证 60/120 日窗口分别计算并落两条记录；数据增量后再次刷新，`updated_at` 更新且旧窗口记录被覆盖（按设计）。

### 10.3 `outcome_service` 单元测试（阶段 4，**最关键**）

- **基础回填**：构造已知涨跌的 10 条历史 episode，direction=多、P(t)=100、P(t+5)=105，验证 `realized_return=0.05`、`hit=True`、`holding_days=5`；direction=空、P(t+5)=105，验证 `hit=False`。
- **【二评】复权序列收益正确性（替换原"换月弃样"用例）**：构造 t→t+5 期间发生主力换月的场景
  （`contract_t=RB2501`、`contract_t_h=RB2505`），验证：
  - `rolled=True`、`actual_contract="RB2505"`；
  - **仍然计算并计入 `compute_stats()` 校准统计**（不再是 `unverifiable_rolled`）；
  - `realized_return` 取自复权连续序列。
- **【二评】复权漂移（P0）**：写入 episode 后人为修改 CSV 使历史价格整体缩放（模拟新增换月点触发后复权重写），
  重跑 `backfill_outcomes()`，验证 `P(t)` 取自**当前 CSV** 而非 `episodes.ref_price`，
  且缩放前后 `realized_return` 保持一致（比例复权下收益率不变）。
- **交易日历**：构造 t 日为春节前最后一交易日、horizon=5 的场景，验证 t+5 跨越春节假期按交易日计算（不是自然日 +5）。
- **MAE 计算**：构造中途最大不利偏移已知的序列，验证 `MAE` 取的是 horizon 内最大不利偏移而非期末值。
- **`unverifiable` 降级**：对无 technical CSV 的品种，验证 `backfill_outcomes()` 标 `unverifiable_reason="no_csv"`，不参与校准统计。
- **校准曲线**：构造 30 个 conf∈[0.5,1.0] 的 episode（各档均匀分布），验证 `compute_stats()` 输出的校准曲线分档胜率与构造值一致。

### 10.4 注入幂等性与一致性测试（阶段 3）

- **同输入同输出**：固定 symbol+date+run_id 与 SQLite 内容，调用两次 `build_context()`，返回文本完全一致（此处只测 ContextBuilder 的拼接，不涉及 LLM）。
- **预算裁剪**：构造远超预算的 episode 数（如 50 条），验证拼接后各段长度 ≤ 配置上限，且按 score 降序保留 top-k；
  **【二评】** 关联品种数超限时按 `related_relevance` 截断到 top-3，且关联段不挤占本品种段预算。
- **首日场景**：对从未分析过的 symbol 调用 `build_context()`，验证 `has_prior=false`、STM 段为空或仅含占位、不抛异常。
- **【二评】`has_prior` 服务端权威**：agent 输出中缺失/伪造 `has_prior` 时，落库值以 ContextBuilder 计算结果为准；
  `memory_refs` 中不存在的 id 被丢弃且不报错。

### 10.5 集成与回归测试（阶段 5+6）

- **端到端**：跑一次 `use_memory=true` 与 `use_memory=false` 的完整分析，对比结果中是否含 `change_vs_last`/`memory_refs`/`has_prior` 字段；`use_memory=false` 时不应出现这些字段。
  **【二评】** 另验：定时分析（`scheduled_service` 构造的请求）`use_memory` 透传生效；
  Word 报告包含新增字段。
- **回填回归**：每周对 resolved episode 重新跑一次 `backfill_outcomes()`，验证 idempotent（已 resolved 的不被重复回填，`updated_at` 不变）。
- **巩固产出回归**：固定输入 episode 集合，跑两次 `consolidate()`，验证第二次不产生重复 semantic（去重生效），且第一次产出的 `draft` 状态在第二次跑时不会被重复创建；
  **【二评】** 并验证水位线：第二次跑因 `last_episode_id` 未推进而**不触发** LLM 调用（`token_usage` 为空）。
- **【二评】关联镜像回归**：同一份 `relation_metrics`，分别对 RB 与 HC 调 `build_context()`，
  验证两端都出现对方且 `corr` 一致、lag 文案方向相反。

### 10.6 测试用例目录建议

新增 `backend/tests/memory/`：
- `test_memory_service.py`
- `test_relation_service.py`
- `test_outcome_service.py`（**P0，复权序列收益 + 复权漂移用例必含**）
- `test_memory_builder.py`
- `test_consolidation.py`
- `conftest.py`（提供 in-memory SQLite fixture 与 synthetic price CSV fixture）

用 `pytest` 运行，CI 中作为阶段验收的硬门禁——任何阶段合并前对应测试全绿。
