---
name: weekly-review
description: 生成跨境电商经营周报与月报（赛狐/SellFox ERP 数据）：按自然周/自然月聚合 GMV、销量、毛利、毛利率、广告成本占比、退款率，计算对月/季/年目标的达成率，给出环比（本周 vs 上周）、对比上月同期与同比，输出 Top/Bottom ASIN 榜与趋势图，并可写入飞书文档。当用户提到"周报""月报""本周复盘""月度复盘""经营复盘""目标达成率""这周生意怎么样""上周数据汇总""本月经营总结""给管理层看的周报/月报""周会或月度经营分析材料""月度总结"时，务必使用本技能，即使用户没有明确说"周报"二字。只读，不修改任何赛狐数据。若用户要的是"某一天"的经营数据（经营日报、巡店、昨日数据），那是日报技能，不要用本技能。
---

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

# 周报 / 月报与目标追踪

把日报的「单点切片」升级为**「趋势 + 结构 + 目标达成」**：同一套只读取数层，换成周/月时间窗，
再加上达成率、环比/同比、Top/Bottom ASIN 榜、趋势图与经营复盘。

## 为什么这样设计

**日报回答「昨天怎么样」，周报/月报回答「这一周/月为什么是这个结果」。** 两者的差别不在数据量，
而在**基线**：

| 日报 | 周报 / 月报 |
|---|---|
| 昨日 vs 近 7 天均值 | 本期 vs 上期 vs 上月同期 vs 去年同期 |
| 单点异常 | 趋势拐点、结构变化 |
| 7 步巡检 | 目标达成率、Top/Bottom 榜、复盘 |

所以本技能**先固定时间窗口径，再取数**（见 `references/workflow.md`）。口径不固定，趋势就是假的。

第二个原则来自赛狐 MCP 的硬约束：**历史测试常见 20 条/页（当前分页以契约为准）**（请求更大值被静默降级、
放大，都不报错），且覆盖真相只看信封 `coverage`（不信 `pagination.has_more`）。这决定了
本技能必须「**先聚合、后下钻**」：从店铺级报表起步，榜单才下钻 MSKU，绝不铺开拉全量。

第三个原则是诚实：赛狐 MCP 拿不到账户健康、买家消息、政策合规、评论原文。**拿不到就标
「⚠️ 需人工核查」，空数据标「无数据」，绝不编造、绝不写 0**。周报/月报是要给管理层做决策的，
一次编造就会失去全部信任。

## 先读这些（按需）

| 文件 | 何时读 |
|---|---|
| `references/workflow.md` | **开始一次生成任务时**：前置校验 → 取数 → 计算 → 渲染 → 推送的完整顺序 |
| `references/report-template.md` | **渲染前**：报告结构、飞书 block 映射、显示规则 |
| `references/metrics-and-fields.md` | **需要确认某指标用哪个字段/工具/口径时**：指标→字段→工具对照表 |
| `references/honest-boundaries.md` | **下结论或用户追问「为什么没有 X」时**：做不到什么、哪些需人工核查 |
| `_shared/SELLFOX_CLIENT.md` | **必读**：MCP 调用约定（contract_version、参数类型、错误语义、分页与 coverage、落盘纪律） |
| `_shared/METRIC_FORMULAS.md` | 指标口径白皮书（利润口径必须显式选择） |
| `_shared/APPROVAL.md` | 人机审核规范（本技能只读，仅报告写入相关小节适用） |

## 执行流程

细化的每一步在 `references/workflow.md`，这里给出骨架与不可跳过的点。

### Step 0：前置校验（不可跳过）

1. `python scripts/skill_paths.py` 能打印 `shared_root`（否则修 `config/paths.yaml`）。
2. 客户端 MCP 就绪：会话内能调 `mcp__Sellfox-MCP__{help,search,action}`（Key 只在客户端
   MCP 配置里，脚本零 HTTP、不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，
   命中指纹时报告加水印并默认阻断写飞书。
3. 读 `_shared/config/reports.yaml` 的 `daily_ops.id`：
   - 为空 → **口径降级**：489 字段宽表（流量/广告/退款/可售天数）不可用，报告必须标注；
   - 并提示用户去赛狐网页端创建报表、按 `fields_hint` 勾选字段、回填 ID 后重跑。
4. `out/raw/` 已有本周期窗口的取数落盘（v3 取数面由 agent 完成，见 Step 2）。

### Step 1：确认参数

周期（周/月）、锚点日期、**利润口径 `settlement`/`shipment`**、店铺范围、折算币种、是否推飞书。
利润口径必须在输出中标注，否则与财务对不上账。

### Step 2：取数（agent 按取数计划落盘，脚本校验组装）

**v3：取数面是 agent**——先 `python scripts/period.py --period week --anchor <日期>` 固定窗口，
再按 `references/workflow.md` 的**取数计划**调 `mcp__Sellfox-MCP__{search,action}`，把每步
完整信封落盘 `out/raw/`（多窗口文件名带窗口标签）：店铺利润×5 窗口
（`profit_shop_current/prev/prev_month/prev_year/mtd.json`）、MSKU 榜单（`profit_msku.json`）、
宽表（`custom_current.json`）、可选趋势（`trend.json`）。落盘后跑校验组装：

```bash
python scripts/fetch_weekly_data.py --period week --anchor 2026-10-12 \
  --profit-caliber settlement --out weekly_data.json
```

- 纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、参数类型按当次
  inputSchema、失配重取一次、不信 has_more 信 coverage、每步落盘不复述数据、多窗口可并行。
- 列表字段名因工具而异：利润报表 `reportList`、宽表 `rows`（解析错会拿到空结果且不报错）。
- **截断必披露**（coverage.truncated），禁止静默截断；空数据标「无数据」，不写 0。

### Step 3：计算

```bash
python scripts/compute_metrics.py --in weekly_data.json --out computed.json
```

阈值读 `thresholds.yaml`，目标值读 `targets.yaml`，**绝不硬编码**。

### Step 4：渲染

```bash
python scripts/render_report.py --in computed.json --out weekly_report.md
```

严格用 `references/report-template.md` 的结构；**「数据说明」区块必填**。

### Step 5（可选）：推送飞书

```bash
python scripts/push_feishu.py --markdown weekly_report.md --computed computed.json
```

默认 `feishu.enabled=false` 不推送；演示环境默认阻断；写入前过 PII 脱敏。

## 不可跳过的硬约束

1. **MCP 访问只走客户端元工具**：`mcp__Sellfox-MCP__{help,search,action}`；技能脚本
   **零 HTTP、零网络**（旧直连客户端 `sellfox_client.py` 已废弃，不得 import），
   Key 只在客户端 MCP 配置里。
2. **action 必带当次 search 返回的 `contract_version`**；失配（`CONTRACT_VERSION_MISMATCH`）
   → 重 search 一次、按新 schema 重建参数、重试一次，不重放原请求。
3. **参数类型按当次 inputSchema**：利润报表（店铺/MSKU）分页 `page_no/page_size` 是
   **integer**，店铺/宽表分页是 string；日期利润报表 `yyyyMMdd`（超 6 个月窗口按契约改
   `yyyyMM`）、宽表 `yyyy-MM-dd`；`profit_caliber_type` 显式传；未声明字段不传。
4. **不信 `pagination.has_more`，信 `coverage` 与 `totalSize`**：截断（coverage.truncated）
   必披露覆盖范围，禁止静默截断。
5. **列表字段名因工具而异**：利润报表 `reportList` / 宽表 `rows`。从契约 `pagination`
   元数据读取，用 `rows` 解析利润报表会拿到空结果**且不报错**。
6. **每步取数立即落盘 `out/raw/`，不在对话中复述行级数据**（上下文是稀缺资源）；
   多窗口文件名带窗口标签，不得混写。
7. **`data:{}` / `null` 是「无数据」，不是失败**：不写 0、不重试。
8. **币种**：顶层 `currency` 是 USD，`inventoryManage.currency` 是 CNY；跨币种**必须显式折算**，
   未折算时按币种分组、禁止直接相加。
9. **PII 脱敏**：订单含 `buyerEmail`/`buyerName`，写入飞书或外部文档前必须脱敏并标注「已脱敏」。
10. **参数外置**：阈值/目标值/report_id 一律读 `_shared/config/`，**绝不硬编码**。
11. **演示环境运行期识别**：命中指纹则报告顶部加水印并**默认阻断写飞书**。
12. **不编造**：拿不到的标「⚠️ 需人工核查」；空数据标「无数据」，**不写 0**。

## 诚实边界（摘要）

- **本技能不做**：账户健康/绩效/买家消息/政策合规/评论原文（MCP 无接口）；
  广告活动级自动归因（属技能 6）；利润逐项深度归因（属技能 5）；任何赛狐侧写操作。
- **需人工核查**：宽表 `salePrice` 与利润报表 `productSales` 口径是否一致；
  `servingStatus=BUDGET_CAPPED`（取值未经实测验证，不得作判定条件）；charData 未返回时的趋势图；
  榜单标识列（`get_profit_report_by_msku` 的 MSKU/ASIN/标题字段名未在 `01/06` 确认，取不到即标需人工核查）。
- **口径降级**：未配置 `report_id` → 流量/广告/退款类指标不可用；
  周报无独立周目标 → 用「本月累计 vs 月目标」并标注。
- **榜单覆盖**：Top/Bottom 受 20/页 + `max_rows` 限制，截断时结论仅供参考。
- 展开见 `references/honest-boundaries.md`。

## 配置

| 配置 | 位置 | 内容 |
|---|---|---|
| 本技能专属 | `config/weekly.yaml` | 周期/周起始、取数安全阀 `limits`、榜单、趋势、字段映射、飞书开关 |
| 路径解析 | `config/paths.yaml` | `shared_root` 绝对路径（**不写死 `../_shared/`**） |
| 预警阈值 | `_shared/config/thresholds.yaml` | 异常判定阈值 |
| 经营目标 | `_shared/config/targets.yaml` | 月/季/年目标 + 达成率分级线 |
| report_id | `_shared/config/reports.yaml` | 自定义报表 ID（一等前置） |
| 店铺/负责人 | `_shared/config/shops.yaml` | 范围与告警路由 |

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具，脚本零网络 |
| action 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 search 拿当次契约 |
| 参数类型沿用旧接口全字符串 | 利润报表被 INVALID_PARAMS 拒绝 | 类型按当次 inputSchema（利润分页是 integer） |
| 传未声明字段 | `additionalProperties` 拒绝 | 只传契约 properties 里的字段 |
| 用 `page_size` 控制返回量 | 静默降级为 20 | 单次 ≤1 页 + max_rows + 截断披露 |
| 用 `rows` 解析利润报表 | 空结果且不报错 | 用 `reportList`（契约 pagination.rows） |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 coverage 与 totalSize |
| 多窗口混写一个落盘文件 | 基线对不上、无法校验 | 文件名带窗口标签，一窗口一盘 |
| 把空数据写成 0 | 误导决策 | 标「无数据」 |
| 跨币种直接相加 | 结果荒谬 | 显式折算或按币种分组 |
| 硬编码阈值/目标 | 被弃用或口径漂移 | 读 `_shared/config/` |
| 把本月累计达成率说成周达成率 | 误导 | 显式标注口径降级 |
| 静默截断榜单 | 被读成「已覆盖全部」 | 在「数据说明」披露 |

## 输出后

询问用户：达成率口径是否符合预期？阈值是否需调整？是否设为每周/每月定时任务？
若报告带演示水印（数据源为沙箱），结论不可用于业务决策 —— 是否需要更换数据源配置？
