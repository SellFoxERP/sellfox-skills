# 转化（conversion）—— 有流量无转化 / 价格劣势 / 评分下滑

> 何时读：分诊命中 P2「有流量无转化」「价格劣势」「评分下滑」时。
> 目标：流量在、购物车在，但**不成交**的原因排查。

## 1. 有流量无转化

**字段**：`sessions`（高）+ `conversionRate`（低）（来源：`get_custom_report_page_list`，**仅宽表可得**）。

判定：
- 仅当 `sessions >= conversion.min_sessions_for_check`（默认 100，避免小样本误报）才判。
- `conversionRate < conversion.category_cvr_pct` → P2。但 **`category_cvr_pct` 默认 null**，因为赛狐 MCP **拿不到类目平均转化率基准**。
- 因此默认行为是：**同店横向对比**——把该 ASIN 的 `conversionRate` 与**同店铺中位水平**比较，低于中位数一定幅度才提示，并**显式标注「类目基准缺失，需人工核查」**。

本技能不取宽表（sessions/conversionRate 由 custom-report-analysis 技能承接）→ 该检查标 **⚠️ 需人工核查**，不得用 `saleNum` 反推转化率；`get_product_analyze_list` 契约实测含会话/转化字段，可作替代源（以当次契约为准）。

**归因方向**（只提示）：
- 主图/标题/五点与搜索意图不匹配
- 价格高于同类（见下节）
- 评分/评论数劣于竞品
- 变体选择混乱、A+ 内容缺失
- 差评激增（`lowReviewNum`，仅宽表）

## 2. 价格劣势

**可用字段**：`listingPrice`（数组形如 `[{"key":"24.99"}]`，来源：`get_online_product_page_list` 与 `get_custom_report_page_list`）、`standardPrice`、`salePrices`（来源：online）。

**关键诚实点**：**赛狐 MCP 没有竞品价格字段**。所以：
- **不能**宣称「高于竞品」。`config/listing.yaml → price.competitor_price_source` 默认 `null`。
- 能做的是：同店铺同 ASIN 的**自身价格变动**提示（`listingPrice` 环比偏离），以及**同店铺同类目价格分布**的离群提示。
- 任何「相对竞品贵/便宜」的结论 → 标 **⚠️ 需人工核查**。

## 3. 评分下滑

**字段**：`rating`、`ratingCount`（来源：`get_online_product_page_list`）；`lowReviewNum`、`reviewNum`（来源：`get_custom_report_page_list`，仅宽表）。

- 判定：`rating` 环比下降超过 `rating.drop_abs`（默认 0.2，实测样例首值 "4.5" 为字符串，取值需转 float）→ P2「评分下滑」。
- 环比需基线：优先用历史快照（见 `workflow.md`）；无快照时只报当前值与评论数，标「无基线，无法判断趋势」。
- `lowReviewNum`（差评数）仅宽表可得（custom-report-analysis 承接）→ 标「需人工核查」。
- **拿不到评论原文/差评文本**（MCP 无接口）→ 归因方向只给假设，明确「具体差评内容需人工核查」。

## 4. 输出格式

```
[P2] ASIN B0XXXX：评分 4.5 → 4.2（-0.3，阈值 0.2）
     字段来源：get_online_product_page_list.rating
     建议：核查近期差评主题（MCP 拿不到评论原文，需人工核查）
```

## 5. 边界

- 本技能**不改**价格、主图、标题、A+ 内容。
- **拿不到**：竞品价格、类目平均 CVR、评论原文、退货原因文本 → ⚠️ 需人工核查。
- 转化类结论依赖窗口字段的口径，输出须标注时间窗口与是否降级。