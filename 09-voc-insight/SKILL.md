---
name: voc-insight
description: 评论 VOC 归因（赛狐/SellFox 官方聚合指标 + 可插拔外部评论 MCP，只读）。先用 lowReviewNum 差评数环比、rating 下降、reviewRate 好评率下滑等聚合指标定位问题 ASIN，再经外部评论 MCP 取评论原文，把差评按质量/尺寸/色差/物流/描述不符聚类归因，产出带行动建议的 VOC 报告（可选写飞书）。只在多个独立评论印证同一主题时才报「模式」，单条只列「个别反馈」。当用户提到"VOC""评论分析""差评""差评原因""为什么评分掉""评分下滑""差评变多""review 分析""买家反馈""客户投诉""评论区都在说什么""描述不符""色差""该改文案还是改产品"时，务必使用本技能，即使用户没说"VOC"二字。注意：赛狐 MCP 拿不到评论原文，原文依赖外部评论 MCP（可插拔）；未接入外部源时降级为「只用聚合指标定位问题 ASIN、声明无原文」，不做主题归因。只读，不改 Listing、不反馈供应链。
---

## 报表自动发现

任务需要自定义报表时读取共享底座 `ONBOARDING.md`：助手先复用已验证选择，否则通过当前 MCP 发现工具，或从自定义指标的 `reportList` 自动提取报表 ID 并核对用途。不要让商家手填 ID、编辑 YAML 或先创建报表。
匹配唯一且字段适用时自动采用；多个候选只让商家选名称；无法取得可用报表则说明缺口并继续简版。内置查询和全局指标盘点不需要报表初始化。用户已选择简版时不重复打扰。

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，九个技能共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，九个技能复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`help/search/action`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 help 或 search；
已有完整契约直接 action，不重复 search。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”仅指赛狐取数后的离线计算；飞书和审批通知模块会联网。
默认输出本地报告；只有用户要求时才启用飞书或外部服务。没有原始数据时仅说明缺口或产出
明确标识的模板，不能把空数据报告当作经营正常的结论。

# 评论 VOC 归因（Comment VOC Insight）

把「哪些 ASIN 评论出问题了」和「为什么」连成一条链：**赛狐聚合指标定位 → 外部评论 MCP 取原文 → 主题聚类归因 → 行动建议**。

## 这个技能做什么 / 不做什么

先说清边界，因为它决定使用者信不信这份报告：

**做**：
- 用赛狐官方聚合指标（零外部依赖、便宜）**定位问题 ASIN**：`lowReviewNum` 环比上升、`rating` 下降、`reviewRate` 下滑。
- 经**可插拔外部评论 MCP** 取评论原文，按主题聚类（质量/尺寸/色差/物流/描述不符/其他）。
- **只在多个独立评论印证同一主题时**报「模式」，否则只列「个别反馈」。
- 产出带行动建议的 VOC 报告；可选写飞书。

**不做**：
- ❌ **不做全量评论归因**。外部评论源多为抽样/受限，结论只代表**抽样范围内**的反馈，不等于总体。
- ❌ **不把单条差评当普遍问题**。这是本技能的核心纪律（见 `references/methodology.md`）。
- ❌ **赛狐官方 MCP 拿不到评论原文**。只有聚合指标；原文靠外部 MCP，**可用性取决于你是否接入**。
  没有原文时**绝不编造主题分布** —— 降级为聚合指标模式并显式声明。
- ❌ **不修改任何 Listing、不反馈供应链系统**。只出建议；执行由人完成。
- ❌ **不直接生图**。优化素材只出「需求单」，生成交给外部生图 MCP（且须先确认成本）。

## 先读这些（按需加载，不要一次全读）

| 文件 | 什么时候读 |
|---|---|
| `references/workflow.md` | **执行前必读**：取数顺序、环比口径、成本闸、归因分层、降级路径、排障 |
| `references/methodology.md` | 判定「该不该报为模式」、解释某条差评为何不算问题时 |
| `references/fields.md` | 核对字段属于哪个工具、判断某字段能否取到时 |
| `references/report-template.md` | **渲染输出前必读**：报告结构、强制披露、诚实边界 |
| `adapters/README.md` | 接入/更换外部评论 MCP 时（可插拔契约） |
| `config/voc.yaml` | 调整阈值/主题词表/成本闸/降级行为时 |
| `config/field-map.yaml` | 需要字段-数据源归属与外部评论契约时 |
| `_shared/SELLFOX_CLIENT.md` | **必读**：MCP 调用约定（三段式、contract_version、类型按契约、coverage、落盘纪律） |

> `_shared/` 是 9 个技能共用底座，**不是技能**。脚本通过本技能 `config/paths.yaml`
> 定位它；优先使用包内 `_shared/`，源码布局回退到同级共享目录。

## 关键字段与数据源（已核对 _shared/FIELD_DICTIONARY.md）

| 字段 | 数据源 | 备注 |
|---|---|---|
| `lowReviewNum` | **仅** `get_custom_report_page_list` | 差评数 —— VOC 核心信号 |
| `reviewNum` | **仅** `get_custom_report_page_list` | 评论总数 |
| `reviewRate` | **仅** `get_custom_report_page_list` | 好评率/评分率 |
| `rating` | `get_custom_report_page_list` + `get_online_product_page_list` | 两者都有 |
| `ratingCount` | `get_custom_report_page_list` + `get_online_product_page_list` | 两者都有 |
| `msku` / `buyBoxPercent` | 仅 custom | 定位变体 / 关联丢购物车 |

> ⚠️ 以为 `lowReviewNum` 在 `get_online_product_page_list` 里会在**不报错**的情况下恒取空值。
> 判断字段是否存在一律查 `_shared/FIELD_DICTIONARY.md` 与当前 MCP 契约，不凭记忆（历史上曾误删真实字段）。

## 执行流程

### 阶段 0：前置校验

- **客户端 MCP 就绪**：会话内可调 `mcp__Sellfox-MCP__{help,search,action}`
  （Key 只在客户端 MCP 配置里，**脚本零 HTTP、不需要 `SELLFOX_MCP_KEY`**）。
- **演示环境**：命中指纹（`MOCK-` / `demo.example.com` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」）
  → 报告顶部加水印，**默认阻断写飞书**。
- **`report_id`**（`_shared/config/reports.yaml → daily_ops.id`）：为空 → **口径降级**：
  `lowReviewNum`/`reviewNum`/`reviewRate` 取不到，仅能用 online 侧 `rating`/`ratingCount`
  （且无环比基线）→ 显式标注，让使用者去赛狐网页端建报表并回填。
- **外部评论源**（`config/voc.yaml → external_reviews`）：未接入/未启用 → 之后走聚合指标模式。

### 阶段 1：定位问题 ASIN（agent 落盘 + 脚本校验组装）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘 `out/raw/`：

- `custom_current.json` / `custom_previous.json` ← `get_custom_report_page_list`
  （宽表两窗口，`lowReviewNum`/`reviewNum`/`reviewRate` 环比，依赖 `report_id`）；
- `online_products.json` ← `get_online_product_page_list`（无 `report_id` 时的降级源，
  仅 `rating`/`ratingCount`）。

然后 `scripts/fetch_signals.py`（**零网络**）校验落盘（存在性/status/空数据/演示指纹/
coverage 截断）并组装 `signals.json`，逐 ASIN 比较 `lowReviewNum` / `rating` / `reviewRate`，
命中信号入选并按影响面排序——`analyze_themes` / `render_report` / `build_optimization_brief`
不感知此次改造。

- 阈值单一真源：`_shared/config/thresholds.yaml → after_sales`（`rating_drop_abs` 等）；
  本技能特有参数在 `config/voc.yaml → discovery`。**绝不硬编码**。
- **调用纪律**（`_shared/SELLFOX_CLIENT.md`）：`action` 必带当次 `search` 返回的
  `contract_version`；参数类型按当次 inputSchema；每步落盘并核对 status，
  **不在对话中复述行级数据**；两窗口取数可并行发起（同 session 并发安全）。
- **`report_id` 缺 → 降级**：只用 online 侧 `rating`/`ratingCount`，标注「无环比基线」。
- **截断必披露**：`coverage.truncated` 写「已达 max_rows，仅覆盖 N 行」；
  `max_flagged_asins` 触顶时写明「本次仅深挖 Top N，其余未纳入」。

### 阶段 2：取评论原文（外部 MCP，先过成本闸）

`scripts/fetch_reviews.py`：

1. **成本闸**：外部源按次/按条收费 → 先报预计调用次数/条数；未 `--confirm-external` → `HOLD`，
   **不发起外部调用**；单价未知标「需人工核查」，**绝不按 0 元估算**。
2. **规范化 + 脱敏**：按 `config/field-map.yaml → external_review_contract` 规范化，
   `reviewer_name` 掩码，保留 `reviewer_id` 用于数独立评论者。
3. 真正的 MCP 调用由 AI 助手 按 `adapters/<你选的>.md` 契约**串行**执行。

### 阶段 3：主题归因（多源印证是核心）

`scripts/analyze_themes.py`：

- **规则初筛**（`config/voc.yaml → themes[*].keywords`）∪ **AI 助手 语义归因**（`--llm-themes`），
  取并集并标注来源。
- **只在多个独立评论印证时才报「模式」**（`pattern`），否则只列「个别反馈」（`mention`）并标注
  「不构成模式」。判定线在 `config/voc.yaml → pattern`。理由见 `references/methodology.md`。
- 无法归类的进 `other`（**不丢弃**）。

### 阶段 4：行动建议与（可选）优化素材

映射在 `analyze_themes.py → THEME_ACTIONS`：
描述不符→改文案图 / 尺寸→加尺码表 / 质量→反馈供应链 / 物流→评估物流商 / 色差→校准主图。

优化素材由 `scripts/build_optimization_brief.py` 产出**需求单（只描述，不生成）**，
默认关闭，生成前须确认成本（与技能 8 同构）。

### 阶段 5：渲染与交付

`scripts/render_report.py` 严格按 `references/report-template.md` 输出，**必须含**：
数据说明（覆盖范围/窗口/字段来源/降级/截断/演示水印）、问题 ASIN、主题归因（两档）、
行动建议、需人工核查清单、诚实边界。

可选写飞书：`scripts/write_feishu.py`，写前 PII 脱敏、演示环境默认阻断、**写后 read-back 校验**。

## 一键运行

```bash
cd sellfox-skills/09-voc-insight
# 前提：agent 已按 references/workflow.md 取数计划把信封落盘到 out/raw/
#（脚本零 HTTP、不需要 SELLFOX_MCP_KEY；Key 只在客户端 MCP 配置里）

# 1) 只跑赛狐侧（零外部依赖）：定位问题 ASIN
python3 scripts/voc_insight.py --days 7

# 2) 已接入外部评论源：先看成本闸，人工确认后喂入原文
python3 scripts/voc_insight.py --reviews-json raw_reviews.json --confirm-external \
    --adapter-file adapters/example_voc_reviews.md

# 3) 逐脚本运行 + 可写飞书（演示环境默认阻断）
python3 scripts/fetch_signals.py --days 7 --out out/signals.json
python3 _shared/scripts/validate_raw.py --dir out/raw \
    --step custom_current:rows --step custom_previous:rows   # 共享落盘校验器（可选）
python3 scripts/fetch_reviews.py --signals out/signals.json --reviews-json raw_reviews.json \
    --confirm-external --out out/reviews.json
python3 scripts/analyze_themes.py --out out/voc_analysis.json
python3 scripts/render_report.py --out out/voc-report.md --print
```

主要输出：`out/signals.json`、`out/reviews.json`、`out/voc_analysis.json`、`out/voc-report.md`。

## 阈值与配置

- 通用阈值：`_shared/config/thresholds.yaml → after_sales`（`rating_drop_abs`、`low_review_daily_max`），
  本技能**不重复定义**。
- 技能专属：`config/voc.yaml`（窗口、信号开关、主题词表、pattern 阈值、成本闸、降级行为）。
- 字段归属：`config/field-map.yaml`；报表 ID：`_shared/config/reports.yaml`。
- **阈值/参数一律读配置，绝不硬编码**；缺失时按 `_shared/SELLFOX_CLIENT.md` §9（参数外置）
  批量补全，
  并标注每个参数的【来源】：配置命中 / 本次填写 / 默认值。

## 诚实边界（不可省略）

写进报告末尾，缺它则报告失去可信度：

- **赛狐官方 MCP 无评论原文** —— 只有聚合指标；原文靠外部 MCP，可用性取决于是否接入。
- **外部评论源多为抽样** —— 结论代表抽样范围内，不等于总体。
- **不做全量归因** —— 只对问题 ASIN 深挖，非全体 ASIN。
- **不把单条差评当模式** —— 未达多源印证阈值的只列「个别反馈」。
- **类目差评率基准拿不到** —— 无法判断「这个差评率算不算高」，只能环比自身。
- **归因粒度** —— 外部源常按父 ASIN，赛狐宽表可能按子 ASIN/msku，须声明粒度。
- **外部源合规与成本** —— 付费/需授权；抓取型源可能有 ToS/法律风险，由使用者确认。
- 空数据标「无数据」，**不写 0**；演示环境数据必须警示。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调赛狐 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具 `mcp__Sellfox-MCP__*`，脚本零网络 |
| action 不带/带旧 `contract_version` | 被网关拒绝 | 每次执行前 `search` 拿当次契约 |
| 参数类型沿用旧接口全字符串 | `INVALID_PARAMS` 拒绝 | 类型按当次 inputSchema（分页 string/integer 因工具而异） |
| 把单条差评报成「模式」 | 误改产品/文案 | pattern/mention 两级门槛 |
| 无原文仍输出主题分布 | **编造** | 降级为聚合指标模式，显式声明 |
| 以为 online 有 `lowReviewNum` | 恒取空值且不报错 | 差评数字段只在 custom（查 14 文档） |
| 落盘缺失仍跑计算面 | 假装取过数 | `voc_insight.py` 先落盘检查；缺步骤按取数计划补齐 |
| 未经确认就调外部评论源 | 产生意外费用 | 成本闸 `HOLD` → 人工确认 |
| 评论昵称直接写飞书 | PII 外泄 | `mask_reviewer` 掩码后再写 |
| 忽略演示水印 | 用 mock 数据做业务决策 | 演示数据只验证连通性 |
| 用 `page_size` 控量 | 服务端静默降级 20 | 取数计划 `max_rows` 安全阀 + coverage 截断披露 |

## 输出后

询问使用者：

1. 主题词表是否覆盖你品类的痛点？（把落入 `other` 的高频词补进 `config/voc.yaml → themes`）
2. `pattern` 阈值（3 条评论 / 2 位评论者）是否合适？高客单品类建议更严。
3. 是否接入/更换外部评论源？（`adapters/` 加一个 md 即可）
4. 是否把本次填写的阈值/报表 ID 回写到配置，供下次复用？（渐进式记忆）
5. 是否需要把报告写入飞书供协作？（演示环境默认阻断）
