---
name: daily-ops-report
description: 生成跨境电商每日经营日报（赛狐/SellFox ERP 数据），按《每日巡店 SOP》7 步顺序巡检账户/订单、销售、广告、库存、转化、售后、政策，产出先结论后明细的本地 HTML 日报（今日结论与行动 → 分项明细 → 附录），不外发。当用户说“经营日报 / 每日日报 / 巡店 / 昨日数据 / 昨天的销售订单广告库存 / 店铺日报 / 每日报表 / 给管理层看的数据 / 生成今天的巡检报告”，或要求把某一天的经营数据整理成文档时，务必使用本技能，即使用户没说“日报”二字。只覆盖单一自然日（默认昨日）的快照与异常归因；不生成周报 / 月报 / 目标达成率（用周报技能）。
---

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，专项技能（01~09）共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，专项技能（01~09）复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`sf_discover/sf_describe/sf_execute`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 sf_discover 或 sf_describe；
已有完整契约直接 sf_execute，不重复 sf_describe。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”指赛狐取数后的离线计算。报告仅本地 HTML，不外发
（飞书写入能力 v1.7.0 已移除；演示数据仅加顶部水印，不外发）。
没有原始数据时仅说明缺口或产出明确标识的模板，不能把空数据报告当作经营正常的结论。

# 每日经营日报

按《每日巡店 SOP》生成跨境电商经营日报，输出本地 HTML 报告（不外发）。

## 何时用 / 何时不用

**用**：需要「某一天」的经营快照与异常归因 —— 昨日 GMV/订单/毛利、广告、库存预警、
转化、退款、以及账户/政策的人工核查提醒。

**不用**：周报 / 月报与目标达成率（用周报技能）；库存补货建议单（用库存技能）；
利润深度诊断（用利润诊断技能）；广告实际调优（用广告技能）；
自定义报表 / 宽表字段分析（流量/转化、广告点击、库龄分桶、补货建议 → `custom-report-analysis` 技能）。

## 为什么这样设计

**第一，顺序就是优先级。** SOP 的元原则是「先风险，后经营；先异常，后优化」。
所以本技能**不按「数据好拿」排序**，而严格按 7 步顺序输出，并把风险项提炼到顶部的
「🔴 今日红线」。赶时间只看「今日结论与行动」，要细节再看「分项明细（七步检查）」。

**第二，诚实是可信度的来源。** SOP 明确声明「不会伪装成店铺实时数据」。
赛狐 MCP **拿不到**账户健康、绩效通知、买家消息、政策合规、评论原文 ——
这些必须标「⚠️ 需人工核查」，**绝不编造**；空数据标「无数据」，**不写 0**（0 与「没拿到」是两回事）。
日报的可信度全靠这一点，一旦掺水就全盘归零。

## 前置校验（先做，别跳过）

调数前确认两件事，任一异常都会影响口径：

1. **客户端 MCP 就绪**：会话内可调 `mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute}`
   （Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，
   技能会运行时识别指纹并在报告顶部加水印（演示数据不外发）。快速自检：
   `python scripts/run_daily_report.py --check-only`。
2. **演示环境指纹**：`MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」。命中 → 报告顶部加水印，
   数值不可用于业务判断（仅加水印，不外发）。

> v1.7.0 起本技能**不再取自定义报表宽表**（get_custom_report_page_list / report_id 依赖已整体移除）。
> 宽表专属指标（流量/转化、广告点击、库龄分桶、补货建议、MSKU 级销量环比、退款/退货全量计数）
> 固定标「⚠️ 需人工核查——由 custom-report-analysis 技能承接」。

## 执行流程

### Step 0：确认参数

| 参数 | 默认 | 来源 |
|---|---|---|
| 日期 | 昨日 | `--date` 或 `daily_report.yaml` 的口径 |
| 店铺范围 | 全部 | `_shared/config/shops.yaml` 的 `default_scope` |
| 利润口径 | `settlement`（结算） | `daily_report.yaml` |
| 聚合币种 | USD | `daily_report.yaml` |
| 输出 | 本地 HTML 主报告（HTML 即报告） | 不外发 |

> ⚠️ **利润口径必须确认并在输出标注**。`settlement` 与 `shipment` 同一时间段结果不同，不标注必与财务对不上账。

### Step 1：取数（agent 按取数计划落盘）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{sf_describe,sf_execute}`，把每步完整信封落盘 `out/raw/<step>.json`：
店铺 → 店铺利润 →（可选）30 天基线切片 → 订单样本 → 广告活动 →（可选）跨平台销量。
纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、参数类型按当次
inputSchema、失配重取一次、不信 has_more 信 coverage、每步落盘不复述数据。
**不承诺固定调用次数**（次数随店铺数增长）。

| 顺序 | 工具 | 列表字段 | 用途 |
|---|---|---|---|
| 1 | `get_shop_page_list` | `data.rows` | 店铺清单（可缓存 7 天） |
| 2 | `get_profit_report_by_shop` | **`data.rows`**（标准化分页） | 店铺级利润 → 结论速览 |
| 2b | `get_profit_report_by_shop`（30 天窗口） | `data.rows`（标准化分页） | 店铺级基线切片（可选） |
| 3 | `get_order_page_list` | `data.rows` | STEP 01 订单异常下钻样本 |
| 4 | `get_sp_campaign_list` | **`data.rows`**（标准化分页）+ 游标 | STEP 03 广告状态 |
| 5 | `get_multiplatform_sale_stats` | `data.rows` | 跨平台销量（默认关闭） |

- 日期格式：利润报表 `yyyyMMdd`（`date_type=2`）；订单 `yyyy-MM-dd HH:mm:ss`。
- **参数类型按当次契约**：店铺分页是 string，利润报表分页是 **integer**（实测差异）。
- **店铺级基线取 30 天切片**（`profit_shop_range`，按日切片算均值）；
  MSKU 级窗口字段（`sevenSaleNum` 等）属宽表，已由 custom-report-analysis 承接。
- **店铺级取数按 `shop_ids` 分批，每批 ≤20 行**（历史测试常见 20 条/页（当前分页以契约为准），靠 `page_size` 控量无效）。
- 详细取数计划与降级路径见 `references/workflow.md`；字段名见 `references/field-map.md`。

### Step 2：异常判定与归因

用 `scripts/analyze.py`（读 bundle.json，零网络）。对核心指标算「昨日 / 基线 / 偏离度」，
超阈值即标异常。**阈值一律读 `_shared/config/thresholds.yaml`，绝不硬编码。**
异常判定范围 = 店铺级利润与 30 天切片基线；宽表专属指标固定标「需人工核查（custom-report-analysis 承接）」。

**红线项必须给归因线索，不能只报数字**：

| 异常 | 归因路径 |
|---|---|
| 销售额跌 | 订单量跌 vs 客单价跌 → 再拆店铺 → 流量/转化归因见 custom-report-analysis |
| ACOS 涨 / CVR 跌 | 宽表指标（adCost/sessions/conversionRate）→ custom-report-analysis 技能承接 |
| 库存预警 | `availableDays`/库龄分桶为宽表字段 → custom-report-analysis 承接；断货补货单见库存技能 |

完整规则与判定禁区见 `references/anomaly-rules.md`（含「`servingStatus=BUDGET_CAPPED` 不得作为判定条件」）。

### Step 3：渲染

用 `scripts/render_report.py`，**严格使用 `references/report-template.md` 的结构**。报告输出 `out/daily_report.html`（主报告，HTML 即报告；`--keep-md` 可附同名 .md 供调试与测试）：

```
📊 每日经营日报 | {日期}
【今日结论与行动】🔴 今日红线（≤5 条） + 核心指标 + 一句话结论
【分项明细（七步检查）】STEP 01 账户(P0) → 02 销售(P0) → 03 广告 → 04 库存 → 05 转化 → 06 售后 → 07 政策
【附录】异常订单明细（PII 已脱敏）
【数据说明】必填，不可省略
```

订单样本的 `buyerEmail` / `buyerName` 渲染前经 `sellfox_offline.mask_pii_in_text` 脱敏并标注「已脱敏」。

### 一键入口

```bash
# 前提：agent 已按 workflow.md 取数计划把信封落盘到 out/raw/
python scripts/run_daily_report.py                     # 校验落盘 → 组装 → 分析 → 渲染
python scripts/run_daily_report.py --date 2026-09-29
python scripts/run_daily_report.py --check-only        # 只做前置校验（含落盘检查）
python _shared/scripts/validate_raw.py --dir out/raw --step shops:rows --step profit_shop:rows
```

## 双读者分层

| 层 | 读者 | 内容 |
|---|---|---|
| 今日结论与行动 | 赶时间只读这层 | 红线（≤5）+ GMV/订单/毛利/毛利率/ACOS/CVR + 一句话结论 |
| 分项明细 | 逐项排查 | 7 步逐项：指标 + 异常 + 归因 + 建议 |
| 附录 | 复核留档 | 异常明细、库存预警清单 |

## 硬约束速查（违反即为缺陷）

- **MCP 访问只走客户端元工具**：`mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute}`，脚本零 HTTP、
  不需要 Key 环境变量（契约见 `_shared/SELLFOX_CLIENT.md`）。
- **sf_execute 必带当次 sf_describe 返回的 contract_version**；失配（`CONTRACT_VERSION_MISMATCH`）
  → 重 sf_describe 一次、按新 schema 重建参数、重试一次，不重放原请求。
- **参数类型按当次 inputSchema**：店铺分页 string、利润报表分页 **integer**；
  未声明字段不传（`additionalProperties` 会拒）。
- **同 session 并发安全**（实测 10 并发 10/10 成功）：多店铺批次可并行；
  每步落盘独立文件并核对 status。
- **列表字段名以当次契约与信封为准**：网关分页默认标准化，根字段为 `rows`（raw 模式才出现 `reportList`/`itemList` 等原始名）；共享库 `rows()` 缺省自动识别 rows/reportList/itemList。
  从契约 `pagination` 元数据读取，用 `rows` 解析利润报表会拿到空结果**且不报错**。
- **不信 `pagination.has_more`，信 `coverage` 与 `totalSize`**（实测 has_more 与 coverage 矛盾）。
- **`data` 空 = 无数据，不是失败**；标「无数据」，不重试，不写 0。
- **status=partial**：读操作可采信但必须披露；涉及写一律按失败（本技能只读，不适用写重放）。
- **币种**：`inventoryManage.currency` 是 CNY，顶层 `currency` 是 USD —— 跨币种不得直接相减。
- **阈值 / 到货周期 / 目标值全部外置**到配置文件，不硬编码。
- **截断必披露**（coverage.truncated），禁止静默截断（静默会被读成「已覆盖全部」，比不跑更危险）。
- **每步取数立即落盘 `out/raw/`，不在对话中复述行级数据**（上下文是稀缺资源）。

## 配置与参数来源

| 配置 | 位置 | 用途 |
|---|---|---|
| 预警阈值 / 红线上限 | `_shared/config/thresholds.yaml` | 异常判定 |
| 店铺与负责人 | `_shared/config/shops.yaml` | 范围与告警路由 |
| 本技能开关 | `config/daily_report.yaml` | 币种、口径、max_rows、时区 |
| 路径 | `config/paths.yaml` | `shared_root` 绝对路径（不写死 `../_shared/`） |

输出中必须标注每个参数的**来源**（配置命中 / 本次填写 / 默认值）；**默认值必须显式标注**。

## 诚实边界（不可省略）

日报末尾**必须**包含数据说明区块，缺少它就失去可信度：

```markdown
### ⚠️ 本日报未能覆盖（赛狐 MCP 无对应数据源）
- 账户健康 / 绩效通知 — 请人工登录 Seller Central 核查
- 买家消息 / 待回工单 — MCP 无客服接口
- 政策合规 / 绩效风险 — MCP 无此接口
- 评论原文 — 仅有聚合指标（好评率 / 差评数），无评论内容
数据截止：{日期} 23:59 ｜ 数据源：赛狐 ERP ｜ 利润口径：settlement
```

- 拿不到 → 标「⚠️ 需人工核查」，**不编造**；空数据 → 标「无数据」，**不写 0**。
- 演示/沙箱环境 → 顶部水印；演示数据仅加水印、不外发（无飞书写入路径）。
- 本技能**不做**：改价、改 Listing、广告调优、补货下单、任何写操作（它是只读技能）；
  自定义报表 / 宽表字段分析（流量/转化、广告点击、库龄分桶、补货建议）→ `custom-report-analysis` 技能。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具，脚本零网络 |
| sf_execute 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 sf_describe 拿当次契约 |
| 参数类型沿用旧接口全字符串 | 利润报表被 INVALID_PARAMS 拒绝 | 类型按当次 inputSchema（利润分页是 integer） |
| 传未声明字段 | `additionalProperties` 拒绝 | 只传契约 properties 里的字段 |
| 依赖 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + max_rows + 截断披露 |
| 按历史字段名硬解列表 | 空结果且不报错 | 列表键用共享库自动识别（rows/reportList/itemList）；网关默认标准化分页根字段为 `rows` |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 coverage 与 totalSize |
| 把空数据写成 0 | 误导决策 | 区分「0」与「无数据」 |
| 硬编码阈值 | 误报率高被弃用 | 读 `thresholds.yaml` |
| 混用 USD 与 CNY | 利润计算错误 | 显式折算并标注汇率 |
| 静默截断 | 被读成「已覆盖全部」 | 在输出披露覆盖范围 |
| 想在日报里看流量/库龄等宽表指标 | 超出本技能边界 | 引导使用 `custom-report-analysis` 技能 |

## 参考文件（何时读）

- `references/workflow.md` —— **取数前读**：取数顺序、批次、降级路径、串行/超时、截断披露。
- `references/field-map.md` —— **核对字段名时读**：每个指标 → 字段 → 所属工具（不凭记忆书写）。
- `references/anomaly-rules.md` —— **判定异常/生成归因时读**：阈值对照、归因路径、判定禁区。
- `references/report-template.md` —— **渲染日报时读**：模板骨架、双读者分层。
- `_shared/SELLFOX_CLIENT.md` —— **必读**：MCP 调用契约（三段式 / contract_version / 参数类型 / 错误语义 / 落盘与上下文预算）。
- `_shared/METRIC_FORMULAS.md` —— 指标口径（利润口径必须显式选择）。

## 与相邻技能的分工

- 自定义报表 / 宽表字段分析（流量/会话/转化、广告点击与花费、库龄分桶、补货建议、
  MSKU 级销量环比与退款计数）→ `custom-report-analysis`。
- 周报 / 月报 / 目标达成率 → `weekly-review`；库存补货建议单 → `inventory-health`；
  利润深度诊断 → `profit-diagnosis`；广告实际调优 → `ad-optimizer`。