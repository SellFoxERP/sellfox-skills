---
name: daily-ops-report
description: 当用户要求“配置赛狐”“配置经营报表”或“初始化赛狐技能”时，引导共享报表配置；也可生成跨境电商每日经营日报（赛狐/SellFox ERP 数据），按《每日巡店 SOP》7 步顺序巡检账户/订单、销售、广告、库存、转化、售后、政策，产出先结论后明细的日报（今日结论与行动 → 分项明细 → 附录），并可选写入飞书文档。当用户说“经营日报 / 每日日报 / 巡店 / 昨日数据 / 昨天的销售订单广告库存 / 店铺日报 / 每日报表 / 给管理层看的数据 / 生成今天的巡检报告”，或要求把某一天的经营数据整理成文档时，务必使用本技能，即使用户没说“日报”二字。只覆盖单一自然日（默认昨日）的快照与异常归因；不生成周报 / 月报 / 目标达成率（用周报技能）。
---

## 报表自动发现

任务需要自定义报表时读取共享底座 `ONBOARDING.md`：助手先复用已验证选择，否则通过当前 MCP 发现工具，或从自定义指标的 `reportList` 自动提取报表 ID 并核对用途。不要让商家手填 ID、编辑 YAML 或先创建报表。
匹配唯一且字段适用时自动采用；多个候选只让商家选名称；无法取得可用报表则说明缺口并继续简版。内置查询和全局指标盘点不需要报表初始化。用户已选择简版时不重复打扰。

## 运行环境与路径

以本文件所在目录为技能根目录，执行脚本前切换到该目录，使用 Python 3.10+。
本项目采用批量安装，专项技能（01~09）共用一份底座；源码布局的底座位于 `../_shared/`。
读取下文 `_shared/...` 引用时先定位实际共享底座。脚本定位优先级为：显式参数（支持时）
→ `SELLFOX_SHARED_ROOT` → `config/paths.yaml` → 包内 `_shared` → 同级 `_shared`。
批量安装器将共享路径写入配置，专项技能（01~09）复用同一份商家配置。
`mcp__Sellfox-MCP__...` 是服务工具的示意名称；使用客户端实际暴露的 Sellfox-MCP
`help/search/action`，不得因客户端前缀不同而判定工具缺失。当前完整契约来自 help 或 search；
已有完整契约直接 action，不重复 search。首先读取共享 `SELLFOX_CLIENT.md`。
下文“零网络”仅指赛狐取数后的离线计算；飞书和审批通知模块会联网。
默认输出本地报告；只有用户要求时才启用飞书或外部服务。没有原始数据时仅说明缺口或产出
明确标识的模板，不能把空数据报告当作经营正常的结论。

# 每日经营日报

按《每日巡店 SOP》生成跨境电商经营日报，默认输出本地 Markdown，可选写入飞书文档。

## 何时用 / 何时不用

**用**：需要「某一天」的经营快照与异常归因 —— 昨日 GMV/订单/毛利、广告、库存预警、
转化、退款、以及账户/政策的人工核查提醒。

**不用**：周报 / 月报与目标达成率（用周报技能）；库存补货建议单（用库存技能）；
利润深度诊断（用利润诊断技能）；广告实际调优（用广告技能）。

## 为什么这样设计

**第一，顺序就是优先级。** SOP 的元原则是「先风险，后经营；先异常，后优化」。
所以本技能**不按「数据好拿」排序**，而严格按 7 步顺序输出，并把风险项提炼到顶部的
「🔴 今日红线」。赶时间只看「今日结论与行动」，要细节再看「分项明细（七步检查）」。

**第二，诚实是可信度的来源。** SOP 明确声明「不会伪装成店铺实时数据」。
赛狐 MCP **拿不到**账户健康、绩效通知、买家消息、政策合规、评论原文 ——
这些必须标「⚠️ 需人工核查」，**绝不编造**；空数据标「无数据」，**不写 0**（0 与「没拿到」是两回事）。
日报的可信度全靠这一点，一旦掺水就全盘归零。

## 前置校验（先做，别跳过）

调数前确认三件事，任一异常都会影响口径：

1. **客户端 MCP 就绪**：会话内可调 `mcp__Sellfox-MCP__{help,search,action}`
   （Key 只在客户端 MCP 配置里，脚本不需要 `SELLFOX_MCP_KEY`）。若数据源为演示/沙箱环境，
   技能会运行时识别指纹并加水印、默认阻断写入（见第 3 条）。快速自检：
   `python scripts/run_daily_report.py --check-only`。
2. **报表选择**：按 `ONBOARDING.md` 自动发现并验证 daily_ops 用途的报表；不可用时跳过宽表，标注明细层指标缺口，不要求手工配置。
3. **演示环境指纹**：`MOCK-` / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」。命中 → 顶部加水印，
   **默认阻断写飞书**。

## 执行流程

### Step 0：确认参数

| 参数 | 默认 | 来源 |
|---|---|---|
| 日期 | 昨日 | `--date` 或 `daily_report.yaml` 的口径 |
| 店铺范围 | 全部 | `_shared/config/shops.yaml` 的 `default_scope` |
| 利润口径 | `settlement`（结算） | `daily_report.yaml` |
| 聚合币种 | USD | `daily_report.yaml` |
| 输出 | 本地 HTML 主报告（附同名 .md 留档） | 写飞书需显式 `--feishu` |

> ⚠️ **利润口径必须确认并在输出标注**。`settlement` 与 `shipment` 同一时间段结果不同，不标注必与财务对不上账。

### Step 1：取数（agent 按取数计划落盘）

**v3：取数面是 agent**——按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘 `out/raw/<step>.json`：
店铺 → 店铺利润 → 宽表（按店铺分批）→ 订单样本 → 广告活动 →（可选）跨平台销量。
纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、参数类型按当次
inputSchema、失配重取一次、不信 has_more 信 coverage、每步落盘不复述数据。
**不承诺固定调用次数**（次数随店铺数增长）。

| 顺序 | 工具 | 列表字段 | 用途 |
|---|---|---|---|
| 1 | `get_shop_page_list` | `data.rows` | 店铺清单（可缓存 7 天） |
| 2 | `get_profit_report_by_shop` | **`data.reportList`** | 店铺级利润 → 结论速览 |
| 3 | `get_custom_report_page_list` | `data.rows` | 489 字段宽表 → 分项明细全部指标 |
| 4 | `get_order_page_list` | `data.rows` | STEP 01 订单异常下钻样本 |
| 5 | `get_sp_campaign_list` | **`data.itemList`** + `nextToken` | STEP 03 广告状态 |
| 6 | `get_multiplatform_sale_stats` | `data.rows` | 跨平台销量（默认关闭） |

- 日期格式：利润报表 `yyyyMMdd`（`date_type=2`）；自定义报表 `yyyy-MM-dd`；订单 `yyyy-MM-dd HH:mm:ss`。
- **参数类型按当次契约**：店铺/宽表分页是 string，利润报表分页是 **integer**（实测差异）。
- **基线用现成窗口字段** `sevenSaleNum` / `thirtySaleNum`（日均 = 窗口值 ÷ 天数），
  **不需要为 7/30 天各发一次调用**。
- **店铺级取数按 `shop_ids` 分批，每批 ≤20 行**（历史测试常见 20 条/页（当前分页以契约为准），靠 `page_size` 控量无效）。
- 详细取数计划与降级路径见 `references/workflow.md`；字段名见 `references/field-map.md`。

### Step 2：异常判定与归因

用 `scripts/analyze.py`（读 bundle.json，零网络）。对每个核心指标算「昨日 / 近 7 天均值 / 近 30 天均值 / 偏离度」，
超阈值即标异常。**阈值一律读 `_shared/config/thresholds.yaml`，绝不硬编码。**

**红线项必须给归因线索，不能只报数字**：

| 异常 | 归因路径 |
|---|---|
| 销售额跌 | 订单量跌 vs 客单价跌 → 再拆店铺/ASIN → 再看 Sessions 跌还是 CVR 跌 |
| CVR 跌 | `buyBoxPercent`（丢购物车？）+ `lowReviewNum`（差评？）+ 价格变动 |
| ACOS 涨 | `cpc`（竞价涨？）+ `adClickRate`（素材衰退？）+ `adConversionRateCpc`（转化跌？） |
| 库存预警 | `availableDays` + `inboundWorking`/`inboundShipped`（在途能否救急） |

完整规则与判定禁区见 `references/anomaly-rules.md`（含「`servingStatus=BUDGET_CAPPED` 不得作为判定条件」）。

### Step 3：渲染

用 `scripts/render_report.py`，**严格使用 `references/report-template.md` 的结构**。报告输出 `out/daily_report.html`（主报告，附同名 .md 留档供飞书通道与文本核对）：

```
📊 每日经营日报 | {日期}
【今日结论与行动】🔴 今日红线（≤5 条） + 核心指标 + 一句话结论
【分项明细（七步检查）】STEP 01 账户(P0) → 02 销售(P0) → 03 广告 → 04 库存 → 05 转化 → 06 售后 → 07 政策
【附录】异常订单/ASIN 明细（PII 已脱敏）、库存预警清单
【数据说明】必填，不可省略
```

### Step 4（可选）：推送飞书

`--feishu` 时用 `_shared/scripts/feishu_writer.py` 写入。**两个必踩的坑**：
① 飞书应用必须被**加为文档协作者**，否则 403；② 创建接口限流 3 次/秒，同文件夹创建须串行。
**写前自动脱敏 `buyerEmail` / `buyerName` 并标注「已脱敏」**；演示环境默认阻断（可 `--override-demo`）。

### 一键入口

```bash
# 前提：agent 已按 workflow.md 取数计划把信封落盘到 out/raw/
python scripts/run_daily_report.py                     # 校验落盘 → 组装 → 分析 → 渲染
python scripts/run_daily_report.py --date 2026-09-29 --feishu
python scripts/run_daily_report.py --check-only        # 只做前置校验（含落盘检查）
python _shared/scripts/validate_raw.py --dir out/raw --step shops:rows --step profit_shop:reportList
```

## 双读者分层

| 层 | 读者 | 内容 |
|---|---|---|
| 今日结论与行动 | 赶时间只读这层 | 红线（≤5）+ GMV/订单/毛利/毛利率/ACOS/CVR + 一句话结论 |
| 分项明细 | 逐项排查 | 7 步逐项：指标 + 异常 + 归因 + 建议 |
| 附录 | 复核留档 | 异常明细、库存预警清单 |

## 硬约束速查（违反即为缺陷）

- **MCP 访问只走客户端元工具**：`mcp__Sellfox-MCP__{help,search,action}`，脚本零 HTTP、
  不需要 Key 环境变量（契约见 `_shared/SELLFOX_CLIENT.md`）。
- **action 必带当次 search 返回的 contract_version**；失配（`CONTRACT_VERSION_MISMATCH`）
  → 重 search 一次、按新 schema 重建参数、重试一次，不重放原请求。
- **参数类型按当次 inputSchema**：店铺/宽表分页 string、利润报表分页 **integer**；
  未声明字段不传（`additionalProperties` 会拒）。
- **同 session 并发安全**（实测 10 并发 10/10 成功）：多店铺批次可并行；
  每步落盘独立文件并核对 status。
- **列表字段名因工具而异**：分页类 `rows` / 利润报表 `reportList` / 广告 `itemList`+`nextToken`。
  从契约 `pagination` 元数据读取，用 `rows` 解析利润报表会拿到空结果**且不报错**。
- **不信 `pagination.has_more`，信 `coverage` 与 `totalSize`**（实测 has_more 与 coverage 矛盾）。
- **`data` 空 = 无数据，不是失败**；标「无数据」，不重试，不写 0。
- **status=partial**：读操作可采信但必须披露；涉及写一律按失败（本技能只读，不适用写重放）。
- **币种**：`inventoryManage.currency` 是 CNY，顶层 `currency` 是 USD —— 跨币种不得直接相减。
- **阈值 / 到货周期 / 目标值 / report_id 全部外置**到配置文件，不硬编码。
- **截断必披露**（coverage.truncated），禁止静默截断（静默会被读成「已覆盖全部」，比不跑更危险）。
- **每步取数立即落盘 `out/raw/`，不在对话中复述行级数据**（上下文是稀缺资源）。

## 配置与参数来源

| 配置 | 位置 | 用途 |
|---|---|---|
| 预警阈值 / 红线上限 | `_shared/config/thresholds.yaml` | 异常判定 |
| 店铺与负责人 | `_shared/config/shops.yaml` | 范围与告警路由 |
| 报表 ID | `_shared/config/reports.yaml` | `reports.daily_ops.id` |
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
- 演示/沙箱环境 → 顶部水印，且默认阻断写飞书。
- 本技能**不做**：改价、改 Listing、广告调优、补货下单、任何写操作（它是只读技能）。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具，脚本零网络 |
| action 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 search 拿当次契约 |
| 参数类型沿用旧接口全字符串 | 利润报表被 INVALID_PARAMS 拒绝 | 类型按当次 inputSchema（利润分页是 integer） |
| 传未声明字段 | `additionalProperties` 拒绝 | 只传契约 properties 里的字段 |
| 依赖 `page_size` 控量 | 服务端静默降级为 20 | 单次 ≤1 页 + max_rows + 截断披露 |
| 用 `rows` 解析利润报表 | 空结果且不报错 | 用 `reportList`（契约 pagination.rows） |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 coverage 与 totalSize |
| 把空数据写成 0 | 误导决策 | 区分「0」与「无数据」 |
| 硬编码阈值 | 误报率高被弃用 | 读 `thresholds.yaml` |
| 拉 489 字段全量 | 上下文爆炸 | 字段裁剪，只取需要的列；落盘不复述 |
| 混用 USD 与 CNY | 利润计算错误 | 显式折算并标注汇率 |
| 静默截断 | 被读成「已覆盖全部」 | 在输出披露覆盖范围 |

## 参考文件（何时读）

- `references/workflow.md` —— **取数前读**：取数顺序、批次、降级路径、串行/超时、截断披露。
- `references/field-map.md` —— **核对字段名时读**：每个指标 → 字段 → 所属工具（不凭记忆书写）。
- `references/anomaly-rules.md` —— **判定异常/生成归因时读**：阈值对照、归因路径、判定禁区。
- `references/report-template.md` —— **渲染日报时读**：模板骨架、双读者分层、飞书 block 映射。
- `_shared/SELLFOX_CLIENT.md` —— **必读**：11 条铁律、MCP 调用契约。
- `_shared/METRIC_FORMULAS.md` —— 指标口径（利润口径必须显式选择）。
- `_shared/config/reports.yaml` —— `report_id` 助手自动发现后可缓存的选择（缺失走降级）。