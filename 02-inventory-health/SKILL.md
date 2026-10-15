---
name: inventory-health
description: 库存健康体检与补货建议（赛狐/SellFox ERP 数据，只读，不下采购单）。逐 MSKU 计算可售天数、在途、到货周期、缺口与建议补货量，输出断货预警（P0/P1/P2）与「断货↔滞销」双向矩阵，生成含「周期来源」列的补货建议单，可写入飞书。当用户提到"库存健康""补货建议""要不要补货""断货预警""缺货风险""可售天数""还能卖几天""库存周转""滞销清理""呆滞库存""库龄""库存体检""补货单"，或询问某个 SKU/MSKU 该不该下补货、哪些货要清时，务必使用本技能，即使用户没有明确说"库存"二字。也适用于每日/每周的例行库存巡检。
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

# 库存健康与补货建议

逐 MSKU 体检库存、预测断货、给出补货量与清货建议。**只出建议，不下采购单**（赛狐 MCP 无采购接口）。

## 为什么这样设计

断货一次 = 排名掉 + 广告白烧 + 竞品抢单，是"直接产生利润"的场景。

核心原则有三条，都来自实测结论：

1. **不重算赛狐已经算好的指标。** `availableDays`（可售天数）、`turnoverDaysMonth`（周转天数）、
   `isrMonth`（库存销售比）、`suggestNumSum`（补货建议量）赛狐官方已经给出。自己造一套算法只会
   与业务方对不上账。本技能**直接采信官方口径**，把精力放在判断、归因、预警和叙述上。
2. **到货周期（lead_time）不猜、不硬编码。** 它按供应商/运输方式/季节变动，所以外置到
   `_shared/config/lead_time.yaml`；缺失时**一次性批量询问**并询问是否回写记忆。用到默认值必须在
   输出里显式标注 —— 使用者要能一眼看出哪些结论建立在不确定假设上。
3. **诚实边界不伪装。** 拿不到的字段标「⚠️ 需人工核查」，空数据标「无数据」（不写 0）。

## 先读这些

| 文档 | 何时读 |
|---|---|
| `references/workflow.md` | **执行前必读**：v3 取数计划（agent 落盘）、降级路径三态、字段裁剪与维度字段格式 |
| `references/replenishment-rules.md` | **计算前必读**：P0/P1/P2 判定公式、lead_time 三级解析与交互、双向矩阵、滞销规则 |
| `references/output-template.md` | **渲染前必读**：库存体检报告 + 补货建议单模板、诚实边界区块 |
| `references/field-sources.md` | 需要核对某字段属于哪个工具、哪些字段需人工核查时 |
| `_shared/SELLFOX_CLIENT.md` | **必读**：v2 agent 调用纪律（三段式 / contract_version / 参数类型 / 错误语义 / 并发与落盘，12 条） |
| `_shared/config/lead_time.yaml` | 解析到货周期（由使用者维护） |
| `_shared/config/thresholds.yaml` | 读取 `inventory` 预警阈值 |
| `_shared/config/reports.yaml` | 读取 `inventory.report_id`（本技能唯一部署前置） |

## 如何执行

```bash
# 前提：agent 已按 references/workflow.md 取数计划把信封落盘到 out/raw/
python scripts/run_inventory_health.py --check-only        # 只做前置校验（含 out/raw 落盘检查）
python scripts/run_inventory_health.py                     # 组装 → 计算 → 渲染（业务日默认今天）
python scripts/run_inventory_health.py --date 2026-09-29 --out out/inventory.md
python scripts/run_inventory_health.py --feishu            # 写入飞书（演示环境默认阻断）
python scripts/run_inventory_health.py --answers '{"MOCK-SKU-001":45}' --write-back  # 本次填写并回写到货周期
```

## 执行流程

### Step 0：前置校验（技能能不能跑）

1. **读 `_shared/config/reports.yaml` 的 `inventory.id`。**
   - 若为空：**主动引导**用户去赛狐网页端「自定义报表」创建一张库存报表，勾选
     `availableDays / 在途 / 库龄分桶 / 周转 / suggestNumSum` 等字段，把报表 ID 回填。
   - 用户暂不回填 → 走**降级路径**：改用 `get_online_product_page_list`。该工具**没有**
     `availableDays`/`suggestNumSum`（二者仅属自定义报表），只能用其真实字段
     `day30SaleNum`/`day7SaleNum`（日均销量）、`inventoryManage.available` 或顶层
     `quantity`/`totalInventory`（可用库存）自算可售天数与建议量，并在输出顶部标注
     「⚠️ 口径降级：缺少自定义报表，可售天数/建议量为自算，非赛狐口径」。缺这些字段的行标
     「⚠️ 需人工核查」，绝不拿自定义报表的 `salesVelocityMonth`/`fbaInventory` 去套（取不到）。
2. **客户端 MCP 就绪与数据环境。** Key 只在客户端 MCP 配置里，脚本零网络、不需要
   `SELLFOX_MCP_KEY`。数据源是沙箱还是生产由客户端配置决定；组装层扫落盘识别演示指纹
   （`MOCK-` 前缀 / `SHOP-DEMO-` / `B0DEMO` / 店铺名含「演示」），命中 → 输出顶部加水印，
   并**默认阻断写飞书**（见 Step 4）。快速自检：`python scripts/run_inventory_health.py --check-only`。
3. 读取阈值与 lead_time 配置。阈值绝不硬编码 —— 不同类目合理值差别很大。

### Step 1：确认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 日期 | 今日 | 用于 lead_time 旺季系数匹配与「预计断货日」推算 |
| 店铺范围 | 落盘数据即口径（agent 取数时定） | 取数参考 `shops.json`；`shops.yaml` 的 `default_scope` 仅作提示 |
| 输出目标 | 本地 Markdown | 是否推送飞书、推到哪个文件夹 |
| 清货营销阈值 | 读配置 | 库龄占比红线读 `thresholds.yaml → inventory` |

> 本技能是**只读**技能：唯一可能的写动作是"把建议单写进飞书文档"，且需人确认。

### Step 2：取数（agent 按取数计划落盘）与组装

**v3：取数面是 agent** —— 按 `references/workflow.md` 的**取数计划**调
`mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘 `out/raw/<step>.json`：

1. `shops.json` ← `get_shop_page_list` —— 店铺清单（分页参数 **string**），为宽表分批
   提供 shop_id；可缓存 7 天。
2. `msku_<shopId>.json` ← `get_custom_report_page_list` —— **主源**（489 字段宽表），
   每店铺批一个文件，窗口 `on_date−report_window_days ~ on_date`（`yyyy-MM-dd`）。
   report_id 缺 → **整步跳过**走降级。单次 ≤1 页（分页大小以当前契约为准）。
3. `online_products_<batch>.json` ← `get_online_product_page_list` —— **降级 / 补充源**
   （`day7SaleNum`/`day30SaleNum`/`inventoryManage`）。⚠️ 顶层 `currency` 是 **USD**，
   其 `inventoryManage.currency` 是 **CNY**，跨币种不得直接相减。
4. `fba_shipments.json` ← `get_fba_shipment_page_list` —— 在途货件明细（时间区间参数
   至少一组完整）。**可能返回 `data: {}` → 标注「无数据」，不是失败，不重试**。
5. `awd_inventory.json` ← `get_awd_inventory_page_list` —— AWD 库存（`page_no`/`page_size`
   必填）。**可能为空**，同上处理。

纪律见 `_shared/SELLFOX_CLIENT.md`：契约先取后用（contract_version）、参数类型按当次
inputSchema、失配重取一次、不信 has_more 信 coverage、每步落盘不复述数据。店铺批次
**可并行**（v3 新网关同 session 并发安全）；**不承诺固定调用次数**（随店铺数与批次增长）。

落盘完成后由 `scripts/fetch_inventory.py` 做**校验 + 组装**（零网络）：演示指纹扫落盘、
`coverage.truncated` 转披露、`data:{}` 标「无数据」；report_id 为空且存在
`online_products_*.json` 时组装降级数据并标「口径降级」。组装结果形状与旧版一致，
后续计算/渲染零改动。

**基线优先用现成窗口字段**（`salesVelocityMonth`、`thirtySaleNum`、`sevenSaleNum`），
不为算基线再发一次调用。✅ `threeSaleNum` 是真实字段（近 3 天窗口，见 `_shared/FIELD_DICTIONARY.md`），可直接用作短基线。

### Step 3：计算（见 `references/replenishment-rules.md`）

对每个 MSKU：

```
日均销量 v   = salesVelocityMonth / 30        （退化：thirtySaleNum/30 → sevenSaleNum/7，并标注来源；
                                               降级源自用 day30SaleNum/30 → day7SaleNum/7）
可售天数 d   = 赛狐 availableDays（降级时 fbmAvailableDays，再不行用在线产品库存/自算）
在途量 inb   = inboundWorking + inboundShipped + inboundReceiving + inTransit
到货周期 L   = lead_time.yaml 三级解析（by_sku > by_supplier > by_shipping_method > default）
              解析不到 → 收集成表【一次性批量询问】→ 询问是否回写配置

IF d < L AND inb == 0        → P0 来不及（空运/海外仓调拨/暂停广告）
ELIF d < L AND inb > 0       → P1 有缺口，缺口 = v*(L-d) - inb，建议量 = max(缺口, suggestNumSum)
                               缺口≤0（在途已覆盖）→ 判「无缺口」，建议量 clamp 到 0，不再标 P1
ELIF d < warn_days(30)       → P2 预警
ELSE                         → 健康
```

滞销侧（库龄 271+ 天占比高 且 动销低）→ 清货建议（降价/促销/移除）。仓位落点见双向矩阵。

**每个参数的来源（配置命中 / 本次填写 / 默认值）都要记录**，写进补货建议单的「周期来源」列。

### Step 4：渲染与（可选）推送

严格按 `references/output-template.md` 输出：

```
📊 库存健康体检 | {日期}

【结论层】
  双向矩阵：① 紧急补货 / ② 健康 / ③ 滞销清理 / ④ 低效观察
  P0 清单（来不及的）／ P1 清单（有缺口的）／ 滞销清单

【补货建议单】
  MSKU / ASIN / 可售天数 / 到货周期 / 周期来源 / 在途 / 缺口 / 建议补货量 / 紧急度 / 负责采购

【数据说明】← 必填：覆盖范围（含截断披露）/ 币种 / 校验口径 / 需人工核查项 / 演示水印
```

推送飞书写入前：

- 过 `feishu_writer.guard_demo()` —— 演示环境**默认阻断**：命中指纹即返回 3 拒绝写入，
  只有显式传 `--allow-demo-write` 才放行，且文档顶部必带演示水印。绝不根据「是否命中指纹」
  自动放行（那等于没有闸门）。
- 本技能不含订单 PII，但仍对任何 `buyerEmail/buyerName` 走 `mask_pii` 兜底。
- 飞书应用须被加为文档协作者，否则 403；创建接口限流 3 次/秒，同文件夹创建须串行。

## 诚实边界（不可省略）

- **不能下采购单。** 赛狐 MCP 无采购接口，输出是建议，执行靠人。
- **不能改安全库存阈值。** 只能读 `domesticSafeStock` / `overseaSafeStock`，不能改。
- **长期仓储费为估算。** 若无配置费率 → 标注「⚠️ 需人工核查」，不编造金额。
- **供应商/运输方式映射拿不到。** MCP 不返回每 SKU 的供应商，需在配置里维护映射；缺失时按默认值算并标注。
- **AWD / FBA 货件可能为空。** 空数据标「无数据」，不写 0，不重试。
- **截断即披露。** 触达 `max_rows` 上限时，必须写明"本次仅覆盖 Top N，其余未纳入"，禁止静默截断。
- 拿不到的字段一律标「⚠️ 需人工核查」，绝不编造。

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 脚本里自己发 HTTP 调 MCP | 双路径漂移、Key 泄漏面扩大 | 取数只走客户端元工具 `mcp__Sellfox-MCP__*`，脚本零 HTTP 零网络 |
| action 不带/带旧 contract_version | 被网关拒绝 | 每次执行前 `search` 拿当次契约 |
| 参数类型沿用旧接口全字符串 | 店铺清单分页传 integer 会被 `INVALID_PARAMS` 拒 | 类型按当次 inputSchema（shops 分页是 **string**） |
| 信 `pagination.has_more` 翻页 | 与 coverage 矛盾导致误判 | 只信 `coverage` 与 `totalSize` |
| 靠 `page_size` 控量 | 服务端静默降级为 20 条/页 | agent 单次 ≤1 页 + max_rows 组装侧截断并披露 |
| 自造可售天数/周转算法 | 与业务方对不上账 | 直接用 `availableDays` / `turnoverDaysMonth` |
| 把 `threeSaleNum` 当作「不存在」而回避 | 它是真实字段（见文档 14），绕开会少一个短基线 | 三天基线用 `threeSaleNum`，七天用 `sevenSaleNum` |
| 维度字段当字符串 | `msku`/`asin`/`shopId` 是**列表**（`["MSKU-..."]` 或 `[{"key":"..."}]`） | 走组装层的 `pick()` 归一化 |
| 默认到货周期混进真实数据 | 误导补货决策 | 「周期来源」列显式标注「默认值⚠️」 |
| USD 售价 − CNY 成本 | 结果荒谬 | 币种显式转换并标注汇率来源与日期 |
| 空数据写 0 | 误导（"没拿到"≠0） | 标「无数据」 |