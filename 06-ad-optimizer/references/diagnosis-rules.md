# 诊断规则与字段来源（diagnosis-rules.md）

> 何时读：需要核对某条规则用什么字段、字段来自哪个工具、阈值键是什么、是否会自动执行时。
> 代码实现在 `scripts/diagnose.py`，本文件与之一一对应。

## 目录

- [一、字段与来源速查](#一字段与来源速查)
- [二、规则表](#二规则表)
- [三、阈值键（全部外置）](#三阈值键全部外置)
- [四、诚实边界：不能作为判定条件的字段](#四诚实边界不能作为判定条件的字段)
- [五、示例](#五示例)

---

## 一、字段与来源速查

诊断分三层，**每个字段都标注所属工具**，避免把不同工具的同名字段混用：

| 字段 | 所属工具 | 列表字段 | 用途 |
|---|---|---|---|
| `campaignId` / `name` / `state` / `budget` / `targetingType` / `servingStatus` | `get_sp_campaign_list` | `itemList` | 活动结构、状态、预算 |
| `keywordId` / `keywordText` / `bid` / `matchType` / `state` | `get_sp_keyword_list` | `itemList` | 关键词结构、竞价 |
| `adId` / `asin` / `sku` / `state` | `get_sp_ad_product_list` | `itemList` | 广告产品结构 |
| `targetId` / `bid` / `expression` / `resolvedExpression` | `get_sp_product_targeting_list` | `itemList` | 商品投放结构 |
| `cost` / `sales` / `orders` / `clicks` / `impressions` / `acos` / `roas` / `click_rate` | `create_ad_download_task`（`adCampaignReport` / `adTargetReport`）→ xlsx | — | 绩效；列名映射见 `config/optimizer.yaml` |
| `adCost` / `adClicks` / `adImpressions` / `acos` / `roas` / `adOrderNum` / `adClickRate` | `get_custom_report_page_list` | `rows` | **兜底**绩效（MSKU 级，不可归因 campaign） |

> 兜底源与主源**不可混用**：`get_custom_report_page_list` 是 MSKU/商品维度，
> 把它当成活动级绩效会得到错误的归因。启用兜底时，活动级/关键词级诊断降级为「人工核查」。

---

## 二、规则表

| # | 规则 key | 级别 | 条件（字段） | 数据来源 | 提案动作 | 默认自动？ |
|---|---|---|---|---|---|---|
| 1 | `high_cost_zero_order` | P0 | `cost >= zero_order_cost_min` 且 `orders == 0` | 活动级绩效 | `edit_sp_campaign`（`state=paused`） | 是（`safety.allow_campaign_state_edit`） |
| 2 | `acos_over` | P1 | `acos > acos_abs_max` 且 `orders >= acos_min_orders` 且 `cost >= zero_order_cost_min` | 活动级绩效 | 无（活动级无竞价字段）→ 提示下钻 | 否（人工核查） |
| 3 | `scale_winning` | P1 | `acos < acos_target` 且 `roas > roas_min` 且 `impressions < scale_max_impressions` | 活动级绩效 + 活动 `budget` | `edit_sp_campaign`（`budget` 上调） | 是（`safety.allow_campaign_budget_edit`） |
| 4 | `creative_decay` | P2 | `click_rate` 环比降幅 `> click_rate_drop_pct` | 活动级绩效（需上期 `__prev_click_rate`） | 无（换素材不在能力范围） | 否（人工处理） |
| 5 | `serving_status_hint` | P2 | 活动 `servingStatus` 非 `CAMPAIGN_ACTIVE` | 活动结构 | 无（仅提示） | 否（人工在后台确认） |
| 6 | `keyword_zero_order` | P0 | 关键词 `cost >= zero_order_cost_min` 且 `orders == 0` | 关键词级绩效 | `edit_sp_targeting`（`state=paused`） | 否（`targetId` 语义未实测确认） |
| 7 | `keyword_acos_over` | P1 | `acos > acos_abs_max` 且 `clicks >= keyword_min_clicks` | 关键词级绩效 + 关键词 `bid` | `edit_sp_targeting`（`bid` 下调） | 否（`safety.allow_keyword_bid_edit`，默认关闭） |
| 8 | `keyword_scale_winning` | P1 | `acos < acos_target` 且 `roas > roas_min` 且 `clicks >= keyword_min_clicks` | 关键词级绩效 + 关键词 `bid` | `edit_sp_targeting`（`bid` 上调） | 否（同上） |

**为什么关键词级默认不自动执行**：`edit_sp_targeting` 要求 `targetId` + `targetingType`，
但 `get_sp_keyword_list` 返回的是 `keywordId`，二者是否等价**未经实测确认**。
拿不准就写，等于拿真金白银试错；所以默认关闭，需人工核对后再打开 `safety.allow_keyword_bid_edit`。

**幅度控制**：竞价/预算单次幅度取 `config/optimizer.yaml` 的 `bid_step_pct` / `budget_step_pct`，
并**夹到** `thresholds.approval.max_budget_change_pct`（±30%）以内；`approval.Proposal.validate`
会再做一次硬校验。

---

## 三、阈值键（全部外置）

所有判定阈值来自 `_shared/config/thresholds.yaml` 的 `ad` 段与 `approval` 段，**不硬编码**：

| 阈值键 | 默认 | 含义 |
|---|---|---|
| `ad.zero_order_cost_min` | 30 | 「高花费」下限（单活动/关键词周期累计花费） |
| `ad.acos_abs_max` | 35 | ACOS 绝对值上限（%） |
| `ad.acos_target` | 25 | 目标 ACOS（%），用于优质扩量判定 |
| `ad.roas_min` | 2.0 | ROAS 下限 |
| `ad.click_rate_drop_pct` | 30 | 点击率环比下降触发素材衰退提示（%） |
| `ad.high_cost_zero_order_days` | 7 | 高花费零转化持续天数（口径说明，非硬判定） |
| `approval.max_items_per_batch` | 20 | 单次变更条数上限 |
| `approval.max_budget_change_pct` | 30 | 单次幅度上限（%） |
| `approval.max_changes_per_campaign_24h` | 2 | 单 campaign 24h 调整次数 |
| `approval.max_net_change_pct_24h` | 30 | 单 campaign 24h 净变化 |
| `approval.max_daily_budget_increment` | 5000 | 全局单日预算增量总额 |

本技能自有参数（`config/optimizer.yaml`）：`proposal.bid_step_pct`、`proposal.budget_step_pct`、
`proposal.keyword_min_clicks`、`proposal.scale_max_impressions`、`proposal.acos_min_orders`、
`scope.*`、`safety.*`。

---

## 四、诚实边界：不能作为判定条件的字段

- **`servingStatus = BUDGET_CAPPED` 未经实测验证**，**不得**作为「预算受限」的自动判定条件。
  规则 5 只在 `servingStatus` 非 `CAMPAIGN_ACTIVE` 时**输出人工提示**，不触发任何写操作。
- **`state` 取值只使用实测确认的 `enabled` / `paused` / `archived`**。
- 账户健康 / 绩效通知 / 政策合规 / 评论原文：MCP 拿不到 → 一律标「需人工核查」，绝不编造。
- 回溯基线：需要「上一期点击率/ACOS」时，若绩效数据只导出了一期，则环比类规则
  （规则 4）自动跳过并标注，**不用本期值冒充基线**。
- `adConversionRateCpc`（广告转化率）经字段字典 06 确认存在于 `get_custom_report_page_list`
  （489 字段，样例 2.33），可用于人工排查转化端，但它是 MSKU 级，**不用于 campaign 级自动判定**。

---

## 五、示例

**示例 1（规则 1）**
输入：活动 C1，周期 `cost=120.5`、`orders=0`，`state=enabled`，阈值 `zero_order_cost_min=30`。
输出：🔴 P0 `high_cost_zero_order` → 提案 `edit_sp_campaign state: enabled → paused`（dry-run）。

**示例 2（规则 3）**
输入：活动 C2，`acos=10%`、`roas=10`、`impressions=300`，`budget=50`；
阈值 `acos_target=25`、`roas_min=2.0`、`scale_max_impressions=1000`、`budget_step_pct=20`。
输出：🟠 P1 `scale_winning` → 提案 `edit_sp_campaign budget: 50 → 60`（+20%，≤±30%）。

**示例 3（规则 8，默认不自动）**
输入：关键词 K2，`acos=45%`、`clicks=30`、`bid=1.0`；阈值 `acos_abs_max=35`、`keyword_min_clicks=10`。
输出：🟠 P1 `keyword_acos_over` → **人工核查**（关键词竞价编辑默认关闭）。

**示例 4（数据缺失）**
输入：绩效行无 `cost` 列（映射匹配不到）。
输出：该指标标「无数据」，规则跳过，**不写 0、不误判为零转化**。