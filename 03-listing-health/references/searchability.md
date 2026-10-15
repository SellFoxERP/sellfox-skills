# 可搜索性（searchability）—— 无流量 / BSR 恶化 / 销量衰退

> 何时读：分诊命中 P1「无流量」或 P2「BSR 恶化」「销量衰退」时。
> 目标：判断 Listing 是否**还能被搜到、还有自然流量**。

## 1. 无流量

**字段**：`sessions`、`pageViews`（来源：`get_custom_report_page_list`，**仅宽表可得**）。

- 判定：`sessions` 连续 `sessions.zero_days` 天（默认 7）为 0 → P1「无流量」。
- **无 report_id 时拿不到 sessions** → 该检查标 **⚠️ 需人工核查**，不得用 `saleNum=0` 之类字段冒充（销量为 0 可能是转化问题，不等于无流量）。
- 空数据（`data:{}`）→ 标「无数据」，**不写 0**，不触发重试。

**归因方向**（仅提示，不改 Listing）：
- 关键词排名跌出首页
- 类目节点错误 / 变体关系断裂
- 被抑制或不可售（先看 `buyability.md`）
- 断货导致搜索权重下滑（看下节）

## 2. BSR 恶化

**字段**：
- `bsrRank`（来源：`get_online_product_page_list`，数组，元素含 `rank` / `bigCategory` / `categoryID` / `categoryName`）——**始终可用**。
- `bigCategoryRanking` / `smallCategoryRanking`（来源：`get_custom_report_page_list`）——仅宽表可得。

判定：
- 取 `bsrRank[].rank`（名次数字**变小=变好，变大=变差**）。名次变差超过 `bsr.rank_drop_pct`（默认 30%）→ P2「BSR 恶化」。
- 无 report_id 时只用 `bsrRank`；类目名次恶化标「需人工核查」。
- ⚠️ 环比需要**基线**。优先用接口现成窗口字段；若只有单期快照，则无法算环比 → 标「仅单期，无法判断趋势，需历史快照」。

**为什么关注 BSR**：BSR 是自然搜索权重的代理指标，恶化通常早于销量下滑显现。

## 3. 销量/流量衰退

**字段**：`day7SaleNum`、`day30SaleNum`、`saleNum`（来源：`get_online_product_page_list`，**现成窗口字段**）。

- 判定：`day7SaleNum/7` 显著低于 `day30SaleNum/30`，且降幅超过 `sales.drop_pct_vs_30d`（默认 30%）→ P2「销量衰退」。
- **优先用现成窗口字段**，不要逐日拉数自算基线（服务端 20 条/页，逐日拉数会瞬间超时预算）。

## 4. 断货拖累排名

- 有 report_id：用 `availableDays`（来源：`get_custom_report_page_list`），低于 `_shared/config/thresholds.yaml → inventory.available_days_min` 提示断货风险。
- 无 report_id：退化为 `quantity`（来源：`get_online_product_page_list`）接近 0 → 标「疑似断货，可用天数需人工核查」。具体补货逻辑属**技能 2（库存健康）**范围，本技能只提示「断货可能拖累排名」。

## 5. 输出与边界

- 每条含 ASIN、指标值、基线、降幅、字段来源工具、建议动作。
- **拿不到**：关键词排名具体位次、竞品流量、类目平均流量 → ⚠️ 需人工核查。
- 本技能**不改**标题/关键词/类目——这些属于上架/Listing 编辑，MCP 亦无接口。