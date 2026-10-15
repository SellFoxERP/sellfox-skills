# 执行流程（workflow.md，v3：agent 取数 + 脚本计算）

> 何时读：**执行本技能前必读**。取数计划、环比口径、外部源成本闸、归因分层、降级路径都在这里。
> **v3 分工**：取数面 = agent 调客户端配置的 `mcp__Sellfox-MCP__{help,search,action}`，
> 按下方「取数计划」把每步**完整信封**落盘 `out/raw/`；计算面 = `fetch_signals.py`（校验+组装）
> → `fetch_reviews.py` → `analyze_themes.py` → `render_report.py`，全部零网络、零 Key。
> 调用纪律（契约版本/参数类型/错误处理/并发）见 `_shared/SELLFOX_CLIENT.md`。

## 目录

- [1. 端到端流程](#1-端到端流程)
- [2. 取数计划（agent 执行）](#2-取数计划agent-执行)
- [3. Step 1 定位问题 ASIN（脚本：校验+组装）](#3-step-1-定位问题-asin脚本校验组装)
- [4. Step 2 取评论原文（外部 MCP，成本闸 + 脱敏）](#4-step-2-取评论原文外部-mcp成本闸--脱敏)
- [5. Step 3 主题归因（多源印证是核心）](#5-step-3-主题归因多源印证是核心)
- [6. Step 4 行动建议与（可选）优化素材](#6-step-4-行动建议与可选优化素材)
- [7. 降级路径](#7-降级路径)
- [8. 失败与排障](#8-失败与排障)

---

## 1. 端到端流程

```
⓪ agent 按取数计划调 mcp__Sellfox-MCP__{search,action}，信封落盘 out/raw/（便宜，零外部依赖）
① fetch_signals.py 校验落盘 + 组装 → lowReviewNum 环比↑ / rating↓ / reviewRate↓ 定位问题 ASIN
② 外部评论 MCP 取评论原文（贵，需授权；先过成本闸；由 agent 按 adapter 契约执行）
③ 主题聚类归因（质量/尺寸/色差/物流/描述不符/其他）
④ 只在多个独立评论印证时 → 报「模式」；否则只列「个别反馈」
⑤ 行动建议 → 描述不符改文案图 / 尺寸加尺码表 / 质量反馈供应链 / 物流换物流商
⑥（可选）生图 MCP 出优化素材（只出需求单，不直接生成）
```

脚本对应：`fetch_signals.py`（⓪①中的计算面）→ `fetch_reviews.py`（②）→ `analyze_themes.py`（③④）
→ `render_report.py`（⑤输出）→ `build_optimization_brief.py`（⑥可选）。
编排入口：`voc_insight.py`（启动先做**落盘检查**，缺步骤给指引）。

> **为什么先聚合后下钻**：赛狐报表分页硬上限 20 条/页，外部评论源又按次/按条收费。
> 只有**先用便宜的聚合指标收敛范围**，才可能把成本和耗时控制在合理区间。
> （赛狐网关同 session 并发安全，多步取数**可并行**发起；外部评论源并发能力未知，默认串行。）

---

## 2. 取数计划（agent 执行）

已知 tool_id **直接 `search` 拿契约**（一次 ≤5 个），再 `action`；action 必带当次
`contract_version`，参数类型以当次 inputSchema 为准。窗口计算（与 `fetch_signals.py` 一致）：
当前窗口 = `[end-N+1, end]`，上一窗口 = `[end-2N+1, end-N]`（`N=--days`，默认取
`config/voc.yaml → stat_windows.short_days`；`end` 默认今天）。

| # | 落盘文件 | 工具 | 关键参数（类型注意） | list_key | max_rows | 说明 |
|---|---|---|---|---|---|---|
| 1 | `out/raw/custom_current.json` | `get_custom_report_page_list` | `report_id`；`start/end_date=当前窗口`（`yyyy-MM-dd`）；`shop_ids`（可选，逗号分隔） | `rows` | 200 | 当前窗口宽表 → lowReviewNum/reviewNum/reviewRate/rating 环比 |
| 2 | `out/raw/custom_previous.json` | 同上 | `start/end_date=上一窗口` | `rows` | 200 | 环比基线窗口；依赖 report_id，缺 → 跳过 1/2，只做步骤 3 |
| 3 | `out/raw/online_products.json` | `get_online_product_page_list` | `online_status_list=["active"]`；`shop_id_list`（可选） | `rows` | 200 | 降级源：仅 rating/ratingCount（**无差评数、无好评率、无历史基线**） |

步骤选择：

- **`report_id` 已配置**（`_shared/config/reports.yaml → daily_ops.id`）→ 落盘步骤 1+2（两窗口环比）；
  步骤 3 可选（仅作 rating/ratingCount 交叉核对，默认不取）。
- **`report_id` 为空** → 跳过 1/2（调了也会被拒），落盘步骤 3，报告走降级路径。

**落盘纪律**（详见 SELLFOX_CLIENT.md §7）：

- 每步 action 的**完整信封**（status/data/coverage/pagination/warnings/trace_id）原样写盘；
- **不在对话中复述行级数据**——数据只落盘，分析交给脚本；
- 单次调用 ≤1 页（多数工具服务端强制 20 条/页，`page_size` 不被尊重）；
  步骤 1/2 两窗口、多店铺批次**可并行发起**，但落盘文件名不冲突、每步单独核对 status；
- `CONTRACT_VERSION_MISMATCH` → 重 search 一次、按新 schema 重建参数、重试一次；
- `INVALID_PARAMS` → 按 `field_errors[{path,rule}]` 修正后重试（失败时**未发业务请求**，安全）；
- 空 data（status=complete）= 无数据 → 落盘原样保留，分析层标「无数据」；
- 翻页前先看 `totalSize`/coverage 是否值得继续，触达 max_rows 必须保留 coverage 让脚本披露。

落盘后可先跑共享校验器做确定性检查：

```bash
python _shared/scripts/validate_raw.py --dir out/raw \
    --step custom_current:rows --step custom_previous:rows --window <当前窗口>
```

---

## 3. Step 1 定位问题 ASIN（脚本：校验+组装）

`scripts/fetch_signals.py`（v3 改造，零网络）：校验 `out/raw/` 落盘（存在性/status/
空数据/演示指纹/coverage 截断），组装出与旧版相同形状的 `signals.json`。

### 3.1 数据源与字段（已核对 _shared/FIELD_DICTIONARY.md）

| 字段 | 所属工具 | 备注 |
|---|---|---|
| `lowReviewNum` | **仅** `get_custom_report_page_list` | 差评数 —— VOC 核心信号 |
| `reviewNum` | **仅** `get_custom_report_page_list` | 评论总数 |
| `reviewRate` | **仅** `get_custom_report_page_list` | 好评率/评分率 |
| `rating` | `get_custom_report_page_list` + `get_online_product_page_list` | 两个工具都有 |
| `ratingCount` | `get_custom_report_page_list` + `get_online_product_page_list` | 两个工具都有 |

⚠️ **`lowReviewNum`/`reviewNum`/`reviewRate` 不在在线产品（79 字段）里** ——
以为「online 也有」会导致恒取空值且不报错。

### 3.2 环比口径

赛狐报表按**日期范围聚合**，`rating` 等字段**不是时间序列**。所以环比的做法是：

- 对比落盘的 `custom_current`（近 N 天）与 `custom_previous`（前 N 天）两段信封，逐 ASIN 相减；
- 主键用 `shopId + asin`（同 ASIN 跨店是不同对象）。

`fetch_signals.py` 的 `--days` 决定 N（默认 `config/voc.yaml → stat_windows.short_days`）。

> ⚠️ **无 report_id 的降级**：online 侧没有差评数字段，且 online **无历史基线**，
> 因此无法做环比。此时技能如实标注「无环比基线」，**不拿别的字段冒充环比**。

### 3.3 阈值

| 参数 | 单一真源 |
|---|---|
| 评分环比下降绝对值 | `_shared/config/thresholds.yaml → after_sales.rating_drop_abs` |
| 单日新增差评上限 | `_shared/config/thresholds.yaml → after_sales.low_review_daily_max` |
| 参与判定的信号开关 | `config/voc.yaml → discovery.signals` |
| 小样本保护（评论数下限） | `config/voc.yaml → discovery.min_rating_count` |
| 差评数入选下限 | `config/voc.yaml → discovery.min_low_review_num` |
| 一次最多深挖 ASIN 数 | `config/voc.yaml → discovery.max_flagged_asins` |

**本技能不重复定义 `rating_drop_abs`**，只读 `_shared`，避免与底座分叉。

### 3.4 排序

候选 ASIN 按「影响面权重 = 销量 × 命中信号数」降序，**仅为排序**。
截断时**必须披露**「本次仅深挖 Top N，其余未纳入」（禁止静默截断）。

---

## 4. Step 2 取评论原文（外部 MCP，成本闸 + 脱敏）

### 4.1 成本闸（不可跳过）

外部评论源**按次/按条计费**。调用前必须：

1. 读 `config/voc.yaml → external_reviews`；
2. 按 adapter 的计费口径（`cost_key`）算预计调用次数/条数；
3. 未传 `--confirm-external` → 闸门 `HOLD`，**不发起外部调用**；
4. 单价未知时标「需人工核查」，**绝不按 0 元估算**；
5. 调用次数 ≤ `max_calls_per_run`（硬上限）。

### 4.2 适配器（不锁定实现）

调用方式由 `adapters/<你选的>.md` 声明，契约见 `adapters/README.md`。
技能**不内置**任何具体评论源。换源只改 adapter。
真正的 MCP 调用由 agent 按 adapter 契约执行（外部源并发能力未知，默认串行），
再用 `--reviews-json` 把结果喂回 `fetch_reviews.py`（该脚本本身零 HTTP）。

### 4.3 规范化与脱敏

`fetch_reviews.py` 按 `config/field-map.yaml → external_review_contract` 规范化：

- **必需字段** `id`/`asin`/`rating`/`text` 缺一即判 invalid（不猜、不编造）；
- `reviewer_name` 掩码（`voc_common.mask_reviewer`，留首尾字符）；
- 保留 `reviewer_id` 用于**数独立评论者**（去重不能用昵称）。

---

## 5. Step 3 主题归因（多源印证是核心）

### 5.1 两层归因

| 层 | 谁做 | 输出 |
|---|---|---|
| 规则初筛 | `analyze_themes.py` 读 `config/voc.yaml → themes[*].keywords` | 可复现、可解释的主题命中 |
| 语义归因 | AI 助手 阅读原文，写入 `--llm-themes` JSON | 词典抓不到的语义（反讽、长句、隐含抱怨） |

两者**取并集**，并在输出标注来源（`rule` / `semantic` / `rule+semantic`）。

> **为什么必须两层**：关键词词典必然漏掉自然语言（"sturdier than expected but sadly…"），
> 但纯语义又不可复现。规则提供基线，语义补覆盖。

### 5.2 ⭐ 多源印证：pattern vs mention

**只在多个独立评论印证时才报告「模式」。** 判定（`config/voc.yaml → pattern`）：

```
pattern  ： 同主题 ≥ min_independent_reviews 条独立评论
           且 ≥ min_distinct_reviewers 位独立评论者
mention  ： 未达线 → 只作「个别反馈」列出，明确标注「不构成模式」
```

一条评论命中多主题时**多标签保留**（不强行单选一个「主因」）。
无法归入已知主题的进 `other`（**不丢弃**，单列以示诚实）。

> **为什么**：把单条差评当普遍问题是最危险的误报 —— 会误导供应链改模具、误导运营改文案。
> 宁可多列「个别反馈」，不可少报门槛。

---

## 6. Step 4 行动建议与（可选）优化素材

| 归因 | 动作 | 归属 |
|---|---|---|
| 描述不符 | 改 Listing 文案/图片使描述与实物一致 | 运营/文案 |
| 尺寸问题 | 加尺寸图/尺码表，核对标注尺寸 | 运营/设计 |
| 色差 | 校准主图、补多角度实拍 | 设计/摄影 |
| 质量问题 | 反馈供应链（来料/工艺/抽检） | 供应链 |
| 物流问题 | 评估物流商/包装 | 物流 |

（映射在 `analyze_themes.py → THEME_ACTIONS`，可按团队习惯调整。）

**优化素材是可选步骤**：`build_optimization_brief.py` 只产出**素材需求单（描述）**，
不直接生图。默认关闭（`optimization_brief.enabled=false`），避免产生生图费用。
生成前必须报数量与成本、人工确认 —— 与技能 8 同构。

---

## 7. 降级路径

技能**逐级降级、绝不崩、绝不编造**：

| 情况 | 行为 | 报告标注 |
|---|---|---|
| `out/raw/` 为空 / 关键步骤落盘缺失 | `fetch_signals.py` 停止并提示按取数计划落盘（`voc_insight.py` 落盘检查先行拦截） | — |
| 无 `report_id` | 只用 online_products.json（agent 落盘步骤 3）：仅 rating/ratingCount，**无差评数、无环比基线**，不报问题 ASIN | 「口径降级」横幅 + 需人工核查 |
| 某信封 `status=failed` | 该源按无数据处理并记录 error_code，其余照常 | 数据说明记录 error_code |
| 信封 data 空（status=complete） | 标「无数据」，**不写 0**，不重试 | 数据说明 |
| 信封 `coverage.truncated` | 披露「已达 max_rows，仅覆盖 N 行，其余未纳入统计」 | 数据说明段 |
| flagged 超过 `max_flagged_asins` | 仅深挖 Top N，其余不纳入 | 「本次仅深挖 Top N，其余未纳入」 |
| 外部源未接入 / 未确认 / 超预算 / 失败 | **聚合指标模式**：定位问题 ASIN，但**不做主题归因** | 顶部横幅 + 第 2 节显式声明 |
| 演示环境命中指纹 | 顶部水印，默认阻断写飞书 | 水印 |

> 降级不等于失真：**没有原文时，你仍能知道「哪些 ASIN 出了问题」，但不知道「为什么」**。
> 技能绝不会在没有原文时编造主题分布。

---

## 8. 失败与排障

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `fetch_signals.py` 报「缺少取数计划落盘」 | agent 未按第 2 节取数计划落盘（或 report_id 与落盘步骤不匹配） | 按取数计划补齐 `out/raw/` 再重跑；先跑 `validate_raw.py` 核对 |
| 所有字段恒空 | 用 `rows` 解析利润报表 | 本技能用 `get_custom_report_page_list`，列表键是 `rows`；确认 tool 名 |
| `lowReviewNum` 总是「无数据」 | 没配 `report_id`，字段只在宽表 | 助手按共享 ONBOARDING.md 自动查找适用报表；无可用报表时仅展示评分快照 |
| `action` 被 `CONTRACT_VERSION_MISMATCH` 拒 | 契约已变更 | 重 `search` 一次、按新 schema 重建参数、重试一次（只一次） |
| `action` 被 `INVALID_PARAMS` 拒 | 参数类型/字段与当次契约不符 | 按 `field_errors[{path,rule}]` 修正；只传契约 properties 里的字段 |
| 外部评论源调用挂起/超时 | 外部 MCP 并发能力未知 | 外部源默认串行调用；赛狐取数走 agent 元工具（并发安全），无此问题 |
| 主题全是 `other` | 关键词词典不覆盖该品类 | 扩充 `config/voc.yaml → themes[*].keywords`，并用 `--llm-themes` 补语义 |
| 差评很多但无 pattern | 阈值偏高或确实零散 | 这是**正确行为**，不要调低到把单条当模式 |
| 报告全是演示水印 | 数据源为演示/沙箱环境（客户端配置所致） | 属数据源配置问题：在客户端改配对应环境的端点/Key；演示数据不能用于业务结论 |
